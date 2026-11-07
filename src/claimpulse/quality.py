"""Step 1b: silver layer with data-quality rules and quarantine.

Rules either QUARANTINE (the record is removed from silver/gold and kept in
dq.quarantine_* with the reason) or FLAG (kept, counted and exposed as a
column). Every rule's hit count is written to dq.rule_results.
"""
from __future__ import annotations

import duckdb

from .config import SOURCES, WAREHOUSE

RULES = [
    ("DQ01", "event", "QUARANTINE", "Event timestamp cannot be parsed"),
    ("DQ02", "event", "QUARANTINE", "Duplicate event id within the same case (later copies removed)"),
    ("DQ03", "event", "QUARANTINE", "Exact duplicate of (case, activity, timestamp) (later copies removed)"),
    ("DQ04", "case", "QUARANTINE", "Case has no claim submission by the employee"),
    ("DQ05", "case", "QUARANTINE", "Claim amount missing or negative"),
    ("DQ06", "case", "QUARANTINE", "Payment Handled earlier than first claim submission"),
    ("DQ07", "event", "FLAG", "Actor role recorded as MISSING"),
    ("DQ08", "case", "FLAG", "Event timestamps not in non-decreasing order within the XES trace"),
    ("DQ09", "case", "FLAG", "Event dated after the log's observed claim-processing horizon (planned trip)"),
    ("DQ10", "case", "FLAG", "Request Payment present but no Payment Handled"),
    ("DQ11", "case", "FLAG", "Claim amount equal to zero"),
    ("DQ12", "case", "FLAG", "Permit-level events shared with another declaration of the same travel permit"),
]

ACT_RE = r"^(Declaration|Permit|Request For Payment) (SUBMITTED|APPROVED|FINAL_APPROVED|REJECTED|SAVED|FOR_APPROVAL) by (.+)$"


def run() -> dict:
    con = duckdb.connect(str(WAREHOUSE))
    con.execute("SET TimeZone='UTC'")
    con.execute("CREATE OR REPLACE TABLE dq.rules (rule_id VARCHAR, level VARCHAR, action VARCHAR, description VARCHAR)")
    con.executemany("INSERT INTO dq.rules VALUES (?,?,?,?)", RULES)

    # ---- typed events ------------------------------------------------------
    con.execute(f"""
    CREATE OR REPLACE TEMP TABLE ev_typed AS
    SELECT e.*,
           try_cast(ts_raw AS TIMESTAMPTZ) AS ts,
           regexp_extract(activity, '{ACT_RE}', 1) AS doc_raw,
           regexp_extract(activity, '{ACT_RE}', 2) AS action_raw,
           regexp_extract(activity, '{ACT_RE}', 3) AS actor_raw
    FROM bronze.event e
    """)
    con.execute("""
    CREATE OR REPLACE TEMP TABLE ev_class AS
    SELECT *,
      CASE WHEN doc_raw IN ('Declaration','Request For Payment') THEN 'CLAIM'
           WHEN doc_raw = 'Permit' THEN 'PERMIT'
           WHEN activity IN ('Start trip','End trip') THEN 'TRIP'
           WHEN activity IN ('Request Payment','Payment Handled') THEN 'PAYMENT'
           WHEN activity = 'Send Reminder' THEN 'REMINDER' ELSE 'OTHER' END AS doc_type,
      CASE WHEN doc_raw <> '' THEN action_raw ELSE NULL END AS action,
      CASE WHEN doc_raw <> '' THEN actor_raw ELSE NULL END AS actor,
      CASE WHEN doc_raw IN ('Declaration','Request For Payment') THEN action_raw || ' by ' || actor_raw
           WHEN doc_raw = 'Permit' THEN 'Permit ' || action_raw || ' by ' || actor_raw
           ELSE activity END AS step,
      row_number() OVER (PARTITION BY log, case_id, event_id ORDER BY event_seq) AS rn_id,
      count(DISTINCT case_id) OVER (PARTITION BY log, event_id) AS n_cases_sharing_id,
      row_number() OVER (PARTITION BY log, case_id, activity, ts ORDER BY event_seq) AS rn_dup
    FROM ev_typed
    """)

    con.execute("""
    CREATE OR REPLACE TABLE dq.quarantine_event AS
    SELECT log, case_id, event_seq, event_id, activity, ts_raw,
           CASE WHEN ts IS NULL THEN 'DQ01' WHEN rn_id > 1 THEN 'DQ02' ELSE 'DQ03' END AS rule_id
    FROM ev_class WHERE ts IS NULL OR rn_id > 1 OR rn_dup > 1
    """)
    con.execute("""
    CREATE OR REPLACE TEMP TABLE ev_clean AS
    SELECT * FROM ev_class WHERE ts IS NOT NULL AND rn_id = 1 AND rn_dup = 1
    """)

    # ---- typed cases -------------------------------------------------------
    con.execute("""
    CREATE OR REPLACE TEMP TABLE case_attr AS
    SELECT log, case_id,
      max(CASE WHEN attr_key = (CASE WHEN log='RFP' THEN 'RequestedAmount' ELSE 'Amount' END) THEN try_cast(attr_value AS DOUBLE) END) AS amount,
      max(CASE WHEN attr_key='RequestedAmount' THEN try_cast(attr_value AS DOUBLE) END) AS requested_amount,
      max(CASE WHEN attr_key='OriginalAmount' THEN try_cast(attr_value AS DOUBLE) END) AS original_amount,
      max(CASE WHEN attr_key='AdjustedAmount' THEN try_cast(attr_value AS DOUBLE) END) AS adjusted_amount,
      max(CASE WHEN attr_key='Permit RequestedBudget' THEN try_cast(attr_value AS DOUBLE) END) AS permit_requested_budget,
      max(CASE WHEN attr_key='BudgetNumber' THEN attr_value END) AS budget_number,
      max(CASE WHEN attr_key IN ('OrganizationalEntity','Permit OrganizationalEntity') THEN attr_value END) AS org_unit,
      max(CASE WHEN attr_key IN ('Project','Permit ProjectNumber') THEN attr_value END) AS project,
      max(CASE WHEN attr_key='Permit ID' THEN attr_value END) AS permit_id,
      max(CASE WHEN attr_key='Cost Type' THEN try_cast(attr_value AS INTEGER) END) AS cost_type
    FROM bronze.trace_attr GROUP BY log, case_id
    """)

    # horizon: last CLAIM or PAYMENT event per log (trip events can be planned dates)
    con.execute("""
    CREATE OR REPLACE TABLE silver.log_horizon AS
    SELECT log, max(ts) AS horizon_ts FROM ev_clean WHERE doc_type IN ('CLAIM','PAYMENT') GROUP BY log
    """)

    con.execute("""
    CREATE OR REPLACE TEMP TABLE case_checks AS
    SELECT c.log, c.case_id, c.amount,
      min(CASE WHEN e.doc_type='CLAIM' AND e.action='SUBMITTED' AND e.actor='EMPLOYEE' THEN e.ts END) AS first_submit,
      min(CASE WHEN e.activity='Payment Handled' THEN e.ts END) AS first_paid,
      count(CASE WHEN e.activity='Request Payment' THEN 1 END) AS n_req_pay,
      count(CASE WHEN e.activity='Payment Handled' THEN 1 END) AS n_paid,
      max(CASE WHEN e.ts > h.horizon_ts THEN 1 ELSE 0 END) AS after_horizon
    FROM case_attr c
    LEFT JOIN ev_clean e USING (log, case_id)
    LEFT JOIN silver.log_horizon h ON h.log = c.log
    GROUP BY c.log, c.case_id, c.amount
    """)
    con.execute("""
    CREATE OR REPLACE TEMP TABLE order_viol AS
    SELECT log, case_id, max(CASE WHEN ts < prev_ts THEN 1 ELSE 0 END) AS out_of_order FROM (
      SELECT log, case_id, ts, lag(ts) OVER (PARTITION BY log, case_id ORDER BY event_seq) AS prev_ts FROM ev_clean
    ) GROUP BY log, case_id
    """)

    con.execute("""
    CREATE OR REPLACE TABLE dq.quarantine_case AS
    SELECT log, case_id, rule_id FROM (
      SELECT log, case_id, 'DQ04' AS rule_id FROM case_checks WHERE first_submit IS NULL
      UNION ALL SELECT log, case_id, 'DQ05' FROM case_checks WHERE amount IS NULL OR amount < 0
      UNION ALL SELECT log, case_id, 'DQ06' FROM case_checks WHERE first_paid < first_submit
    )
    """)

    con.execute("""
    CREATE OR REPLACE TABLE silver.case AS
    SELECT a.*,
      coalesce(o.out_of_order,0)=1 AS dq08_out_of_order,
      k.after_horizon=1 AS dq09_after_horizon,
      (k.n_req_pay > 0 AND k.n_paid = 0) AS dq10_request_without_payment,
      (a.amount = 0) AS dq11_zero_amount,
      coalesce(sh.shared,false) AS dq12_shared_permit_events
    FROM case_attr a
    LEFT JOIN (SELECT log, case_id, bool_or(n_cases_sharing_id > 1) AS shared FROM ev_clean GROUP BY 1,2) sh USING (log, case_id)
    JOIN case_checks k USING (log, case_id)
    LEFT JOIN order_viol o USING (log, case_id)
    WHERE (a.log, a.case_id) NOT IN (SELECT log, case_id FROM dq.quarantine_case)
    """)
    con.execute("""
    CREATE OR REPLACE TABLE silver.event AS
    SELECT e.log, e.case_id, e.event_seq, e.event_id, e.activity, e.ts AS ts_utc,
           timezone('Europe/Amsterdam', e.ts) AS ts_local,
           e.resource, e.role, e.doc_type, e.action, e.actor, e.step,
           e.role = 'MISSING' AS dq07_role_missing
    FROM ev_clean e
    WHERE (e.log, e.case_id) IN (SELECT log, case_id FROM silver.case)
    """)

    # ---- rule results ------------------------------------------------------
    counts = {}
    for rid, _, _, _ in RULES:
        counts[rid] = 0
    for rid, n in con.execute("SELECT rule_id, count(*) FROM dq.quarantine_event GROUP BY 1").fetchall():
        counts[rid] = n
    for rid, n in con.execute("SELECT rule_id, count(DISTINCT log||case_id) FROM dq.quarantine_case GROUP BY 1").fetchall():
        counts[rid] = n
    counts["DQ07"] = con.execute("SELECT count(*) FROM silver.event WHERE dq07_role_missing").fetchone()[0]
    for col, rid in [("dq08_out_of_order", "DQ08"), ("dq09_after_horizon", "DQ09"),
                     ("dq10_request_without_payment", "DQ10"), ("dq11_zero_amount", "DQ11"),
                     ("dq12_shared_permit_events", "DQ12")]:
        counts[rid] = con.execute(f"SELECT count(*) FROM silver.case WHERE {col}").fetchone()[0]
    con.execute("CREATE OR REPLACE TABLE dq.rule_results (rule_id VARCHAR, hits BIGINT)")
    con.executemany("INSERT INTO dq.rule_results VALUES (?,?)", list(counts.items()))

    res = {
        "bronze_cases": con.execute("SELECT count(DISTINCT log||case_id) FROM bronze.trace_attr").fetchone()[0],
        "bronze_events": con.execute("SELECT count(*) FROM bronze.event").fetchone()[0],
        "silver_cases": con.execute("SELECT count(*) FROM silver.case").fetchone()[0],
        "silver_events": con.execute("SELECT count(*) FROM silver.event").fetchone()[0],
        "quarantined_cases": con.execute("SELECT count(DISTINCT log||case_id) FROM dq.quarantine_case").fetchone()[0],
        "quarantined_events": con.execute("SELECT count(*) FROM dq.quarantine_event").fetchone()[0],
        "rule_hits": counts,
    }
    con.close()
    return res
