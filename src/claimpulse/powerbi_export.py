"""Step 5b: Power BI star-schema export (CSV) plus DAX measures.

Grain:
- fact_claim: one row per claim (derived, no raw event payloads)
- fact_transition: one row per hand-off between consecutive claim steps
  (written locally, excluded from the GitHub zip because it is event-level)
Dimensions: dim_date, dim_log, dim_org, dim_step, dim_rag.
"""
from __future__ import annotations

import duckdb
import pandas as pd

from .audit import Audit
from .config import OUT, WAREHOUSE

D = OUT / "powerbi"


def claim_fact(con) -> pd.DataFrame:
    df = con.execute("""
      SELECT row_number() OVER (ORDER BY c.log, c.first_submit_ts, c.case_id) AS claim_key,
        c.log AS log_code, c.case_id,
        CAST(strftime(c.first_submit_local, '%Y%m%d') AS INTEGER) AS submit_date_key,
        CAST(strftime(timezone('Europe/Amsterdam', c.payment_handled_ts), '%Y%m%d') AS INTEGER) AS payment_date_key,
        c.submit_month,
        coalesce(c.org_unit, 'DD ' || c.budget_number) AS org_code,
        c.outcome, round(c.cycle_days, 6) AS cycle_days, round(c.approval_days, 6) AS approval_days,
        round(c.payment_days, 6) AS payment_days, c.n_submissions, c.n_org_rejections, c.n_rej_admin, c.n_rej_approver,
        c.rejected_any::INT AS rejected_any, c.resubmitted::INT AS resubmitted,
        c.first_time_right::INT AS first_time_right, c.sla_breach::INT AS sla_breach,
        c.problem_claim::INT AS problem_claim, c.director_step, c.budget_owner_step,
        round(c.amount, 2) AS amount
      FROM gold.claim c WHERE NOT c.censored""").df()
    fit = pd.read_csv(OUT / "mining" / "alignment_fitness_by_case.csv")
    fit[["log_code", "case_id"]] = fit.case_key.str.split("|", n=1, expand=True)
    df = df.merge(fit[["log_code", "case_id", "fitness", "fits"]].rename(
        columns={"fitness": "alignment_fitness", "fits": "conforms_to_reference"}), how="left")
    df["conforms_to_reference"] = df.conforms_to_reference.astype("boolean").astype("Int64")
    rag = pd.read_csv(OUT / "model" / "rag_monitor_claims.csv")[["log", "case_id", "risk_score", "rag", "top_risk_drivers"]]
    df = df.merge(rag.rename(columns={"log": "log_code"}), how="left")
    df["rag"] = df.rag.fillna("NOT SCORED")
    return df


def run():
    a = Audit("step5_powerbi")
    D.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    fact = claim_fact(con)
    fact.drop(columns=["submit_month"]).to_csv(D / "fact_claim.csv", index=False)

    tr = con.execute("""
      SELECT t.log AS log_code, t.case_id, t.from_step, t.to_step,
             CAST(strftime(timezone('Europe/Amsterdam', t.from_ts), '%Y%m%d') AS INTEGER) AS from_date_key,
             round(t.wait_hours, 3) AS wait_hours
      FROM gold.transition t JOIN gold.claim c USING (log, case_id) WHERE NOT c.censored""").df()
    steps = sorted(set(tr.from_step) | set(tr.to_step))
    dim_step = pd.DataFrame({"step_key": range(1, len(steps) + 1), "step": steps})
    dim_step["actor_role"] = dim_step.step.str.extract(r" by (.+)$")[0].fillna("SYSTEM")
    dim_step["action"] = dim_step.step.str.extract(r"^(\w+) by ")[0].fillna(dim_step.step)
    sk = dict(zip(dim_step.step, dim_step.step_key))
    tr = tr.merge(fact[["log_code", "case_id", "claim_key"]])
    tr["from_step_key"] = tr.from_step.map(sk)
    tr["to_step_key"] = tr.to_step.map(sk)
    tr["handoff"] = tr.from_step + " > " + tr.to_step
    tr[["claim_key", "from_step_key", "to_step_key", "handoff", "from_date_key", "wait_hours"]].to_csv(D / "fact_transition.csv", index=False)
    dim_step.to_csv(D / "dim_step.csv", index=False)

    dates = pd.date_range(min(fact.submit_month.min(), pd.Timestamp("2017-01-01")), "2019-12-31", freq="D")
    dim_date = pd.DataFrame({"date_key": dates.strftime("%Y%m%d").astype(int), "date": dates.date,
                             "year": dates.year, "quarter": "Q" + dates.quarter.astype(str), "month_no": dates.month,
                             "month": dates.strftime("%b"), "year_month": dates.strftime("%Y-%m"),
                             "weekday_no": dates.isocalendar().day.values, "weekday": dates.strftime("%a"),
                             "scope": ["Two departments" if y == 2017 else "Whole university" for y in dates.year]})
    dim_date.to_csv(D / "dim_date.csv", index=False)
    pd.DataFrame({"log_code": ["DD", "ID", "RFP"],
                  "log_name": ["Domestic declarations", "International declarations", "Requests for payment"],
                  "needs_travel_permit": [0, 1, 0]}).to_csv(D / "dim_log.csv", index=False)
    org = fact.groupby("org_code").agg(first_log=("log_code", "min"), claims=("claim_key", "size")).reset_index()
    org["org_type"] = org.org_code.str.startswith("DD ").map({True: "Budget (domestic, no org unit)", False: "Organisational unit"})
    org.insert(0, "org_key", range(1, len(org) + 1))
    org.drop(columns=["claims"]).to_csv(D / "dim_org.csv", index=False)
    pd.DataFrame({"rag": ["RED", "AMBER", "GREEN", "NOT SCORED"], "rag_order": [1, 2, 3, 4],
                  "rag_hex": ["#d03b3b", "#fab219", "#0ca30c", "#9b9a95"]}).to_csv(D / "dim_rag.csv", index=False)

    a.put("pbi_fact_claim_rows", len(fact), "int", "fact_claim.csv")
    a.put("pbi_fact_transition_rows", len(tr), "int", "fact_transition.csv")
    a.put("pbi_dim_org_rows", len(org), "int", "dim_org.csv")
    a.put("pbi_dim_step_rows", len(dim_step), "int", "dim_step.csv")
    con.close()
    a.save([f"outputs/powerbi/{f}" for f in ["fact_claim.csv", "fact_transition.csv", "dim_date.csv", "dim_log.csv",
                                            "dim_org.csv", "dim_step.csv", "dim_rag.csv"]])
    return fact
