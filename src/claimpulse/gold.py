"""Step 1c: gold layer. One row per claim plus a claim-step transition table.

Definitions (also in docs/reporting_requirements.md):
- submission: "<doc> SUBMITTED by EMPLOYEE"; first submission starts the clock.
- organisational rejection: REJECTED by any review role (not by EMPLOYEE).
- resubmission (rework): more than one submission on the same claim.
- outcome: PAID if Payment Handled exists; REJECTED_CLOSED if not paid and the
  last claim event is a rejection; otherwise OPEN.
- cycle time: first submission to last Payment Handled, calendar days.
"""
from __future__ import annotations

import duckdb

from .config import CENSOR_DAYS, REVIEW_ROLES, SLA_DAYS, WAREHOUSE


def run() -> dict:
    con = duckdb.connect(str(WAREHOUSE))
    con.execute("SET TimeZone='UTC'")
    roles = ",".join(f"'{r}'" for r in REVIEW_ROLES)

    con.execute(f"""
    CREATE OR REPLACE TABLE gold.claim AS
    WITH ev AS (SELECT * FROM silver.event),
    claim_ev AS (SELECT * FROM ev WHERE doc_type = 'CLAIM'),
    last_claim AS (
      SELECT log, case_id, arg_max(action, ts_utc) AS last_action FROM claim_ev GROUP BY 1,2
    ),
    agg AS (
      SELECT e.log, e.case_id,
        min(CASE WHEN doc_type='CLAIM' AND action='SUBMITTED' AND actor='EMPLOYEE' THEN ts_utc END) AS first_submit_ts,
        min(CASE WHEN doc_type='CLAIM' AND action='SUBMITTED' AND actor='EMPLOYEE' THEN ts_local END) AS first_submit_local,
        count(CASE WHEN doc_type='CLAIM' AND action='SUBMITTED' AND actor='EMPLOYEE' THEN 1 END) AS n_submissions,
        count(CASE WHEN doc_type='CLAIM' AND action='REJECTED' AND actor IN ({roles}) THEN 1 END) AS n_org_rejections,
        count(CASE WHEN doc_type='CLAIM' AND action='REJECTED' AND actor='ADMINISTRATION' THEN 1 END) AS n_rej_admin,
        count(CASE WHEN doc_type='CLAIM' AND action='REJECTED' AND actor IN ('SUPERVISOR','BUDGET OWNER','PRE_APPROVER','DIRECTOR') THEN 1 END) AS n_rej_approver,
        min(CASE WHEN doc_type='CLAIM' AND action='FINAL_APPROVED' THEN ts_utc END) AS final_approved_ts,
        max(CASE WHEN doc_type='CLAIM' AND action='FINAL_APPROVED' AND actor='DIRECTOR' THEN 1 ELSE 0 END) AS director_step,
        max(CASE WHEN doc_type='CLAIM' AND actor='BUDGET OWNER' AND action='APPROVED' THEN 1 ELSE 0 END) AS budget_owner_step,
        max(CASE WHEN doc_type='CLAIM' AND actor='PRE_APPROVER' AND action='APPROVED' THEN 1 ELSE 0 END) AS pre_approver_step,
        min(CASE WHEN activity='Request Payment' THEN ts_utc END) AS request_payment_ts,
        max(CASE WHEN activity='Payment Handled' THEN ts_utc END) AS payment_handled_ts,
        count(CASE WHEN doc_type IN ('CLAIM','PAYMENT') THEN 1 END) AS n_claim_events,
        count(CASE WHEN activity='Send Reminder' THEN 1 END) AS n_reminders,
        min(CASE WHEN activity='Start trip' THEN ts_utc END) AS trip_start_ts,
        min(CASE WHEN activity='End trip' THEN ts_utc END) AS trip_end_ts,
        min(CASE WHEN doc_type='PERMIT' AND action='SUBMITTED' THEN ts_utc END) AS permit_submit_ts,
        min(CASE WHEN doc_type='PERMIT' AND action='FINAL_APPROVED' THEN ts_utc END) AS permit_final_ts,
        count(CASE WHEN doc_type='PERMIT' AND action='REJECTED' AND actor IN ({roles}) THEN 1 END) AS n_permit_rejections
      FROM ev e GROUP BY 1,2
    )
    SELECT a.*, c.amount, c.requested_amount, c.original_amount, c.adjusted_amount, c.permit_requested_budget,
      c.budget_number, c.org_unit, c.project, c.permit_id, c.cost_type,
      c.dq11_zero_amount, c.dq12_shared_permit_events,
      CASE WHEN a.payment_handled_ts IS NOT NULL THEN 'PAID'
           WHEN l.last_action = 'REJECTED' THEN 'REJECTED_CLOSED' ELSE 'OPEN' END AS outcome,
      date_trunc('month', a.first_submit_local)::DATE AS submit_month,
      date_diff('second', a.first_submit_ts, a.payment_handled_ts) / 86400.0 AS cycle_days,
      date_diff('second', a.first_submit_ts, a.final_approved_ts) / 86400.0 AS approval_days,
      date_diff('second', a.final_approved_ts, a.payment_handled_ts) / 86400.0 AS payment_days,
      a.n_org_rejections > 0 AS rejected_any,
      a.n_submissions > 1 AS resubmitted,
      (a.first_submit_ts >= h.horizon_ts - INTERVAL {CENSOR_DAYS} DAY) AS censored,
      h.horizon_ts
    FROM agg a
    JOIN silver.case c USING (log, case_id)
    LEFT JOIN last_claim l USING (log, case_id)
    JOIN silver.log_horizon h ON h.log = a.log
    """)
    con.execute(f"""
    CREATE OR REPLACE TABLE gold.claim AS
    SELECT *,
      (outcome='PAID' AND cycle_days > {SLA_DAYS}) AS sla_breach,
      (outcome='PAID' AND NOT rejected_any AND NOT resubmitted) AS first_time_right,
      -- early-warning target: the claim needed attention (rejected, paid late, or not paid)
      (rejected_any OR outcome <> 'PAID' OR cycle_days > {SLA_DAYS}) AS problem_claim
    FROM gold.claim
    """)

    con.execute("""
    CREATE OR REPLACE TABLE gold.transition AS
    SELECT * FROM (
      SELECT log, case_id, step AS from_step,
        lead(step) OVER w AS to_step, ts_utc AS from_ts, lead(ts_utc) OVER w AS to_ts,
        date_diff('second', ts_utc, lead(ts_utc) OVER w) / 3600.0 AS wait_hours
      FROM silver.event WHERE doc_type IN ('CLAIM','PAYMENT')
      WINDOW w AS (PARTITION BY log, case_id ORDER BY ts_utc, event_seq)
    ) WHERE to_step IS NOT NULL
    """)
    res = {r[0]: r[1] for r in con.execute("SELECT log, count(*) FROM gold.claim GROUP BY 1").fetchall()}
    con.close()
    return res
