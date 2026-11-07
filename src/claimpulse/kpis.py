"""Step 2: operational KPIs (volumes, cycle time, approval and rejection, rework,
SLA breaches) by log, by month, by reviewing role and by organisational unit."""
from __future__ import annotations

import duckdb
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .audit import Audit
from .config import FIG_DIR, OUT, SLA_DAYS, SLA_SENSITIVITY, SLA_STATUS_AMBER, SLA_STATUS_RED, WAREHOUSE
from .viz import INK2, LOG_COLOURS, LOG_LABELS, SEQ_BLUE, save, style

ORG_MIN = 50  # minimum claims for an org unit or budget to appear in a scorecard

KPI_SELECT = """
  count(*) AS claims,
  avg((outcome='PAID')::INT) AS paid_rate,
  avg(rejected_any::INT) AS org_rejection_rate,
  avg((outcome='REJECTED_CLOSED')::INT) AS rejected_closed_rate,
  avg(resubmitted::INT) AS resubmission_rate,
  avg(first_time_right::INT) AS first_time_right_rate,
  median(cycle_days) AS median_cycle_days,
  quantile_cont(cycle_days, 0.9) AS p90_cycle_days,
  avg(cycle_days) AS mean_cycle_days,
  avg(CASE WHEN outcome='PAID' THEN sla_breach::INT END) AS sla_breach_rate,
  median(approval_days) AS median_approval_days,
  median(payment_days) AS median_payment_days
"""


def run():
    style()
    a = Audit("step2_kpis")
    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    base = "FROM gold.claim WHERE NOT censored"
    d = OUT / "kpis"
    arts = []

    overall = con.execute(f"SELECT 'ALL' AS log, {KPI_SELECT} {base}").df()
    by_log = con.execute(f"SELECT log, {KPI_SELECT} {base} GROUP BY log ORDER BY log").df()
    tbl = pd.concat([by_log, overall], ignore_index=True)
    tbl.to_csv(d / "kpi_by_log.csv", index=False)
    arts.append("outputs/kpis/kpi_by_log.csv")
    for _, r in tbl.iterrows():
        p = r["log"]
        a.put(f"{p}_claims", int(r["claims"]), "int", "kpi_by_log.csv")
        for c in ["paid_rate", "org_rejection_rate", "rejected_closed_rate", "resubmission_rate",
                  "first_time_right_rate", "sla_breach_rate"]:
            a.put(f"{p}_{c}", float(r[c]), "pct", "kpi_by_log.csv")
        for c in ["median_cycle_days", "p90_cycle_days", "mean_cycle_days", "median_approval_days", "median_payment_days"]:
            a.put(f"{p}_{c}", float(r[c]), "d1", "kpi_by_log.csv")
    a.put("sla_days", SLA_DAYS, "int", "config.SLA_DAYS")
    a.put("sla_sensitivity_list", ", ".join(str(x) for x in SLA_SENSITIVITY[:-1]) + f" and {SLA_SENSITIVITY[-1]}",
          source="config.SLA_SENSITIVITY")
    a.put("org_min_claims", ORG_MIN, "int", "kpis.ORG_MIN")
    a.put("sla_status_amber", SLA_STATUS_AMBER, "pct", "config.SLA_STATUS_AMBER")
    a.put("sla_status_red", SLA_STATUS_RED, "pct", "config.SLA_STATUS_RED")
    a.put("sla_status_amber_dec", SLA_STATUS_AMBER, "d2", "config.SLA_STATUS_AMBER")
    a.put("sla_status_red_dec", SLA_STATUS_RED, "d2", "config.SLA_STATUS_RED")
    a.put("sla_dd_within_share", con.execute(f"SELECT avg((cycle_days <= {SLA_DAYS})::INT) {base} AND outcome='PAID' AND log='DD'").fetchone()[0],
          "pct", "gold.claim")
    zr = con.execute(f"SELECT count(*), avg(problem_claim::INT), avg((outcome<>'PAID')::INT) {base} AND amount = 0").fetchone()
    a.put("zero_amount_claims", zr[0], "int", "gold.claim")
    a.put("zero_amount_problem_rate", zr[1], "pct", "gold.claim")
    a.put("zero_amount_unpaid_rate", zr[2], "pct", "gold.claim")

    # monthly
    monthly = con.execute(f"SELECT submit_month, log, {KPI_SELECT} {base} GROUP BY ALL ORDER BY 1,2").df()
    monthly.to_csv(d / "kpi_monthly.csv", index=False)
    arts.append("outputs/kpis/kpi_monthly.csv")
    worst = monthly[monthly.claims >= 100].sort_values("sla_breach_rate", ascending=False).iloc[0]
    a.put("worst_sla_month", worst.submit_month.strftime("%B %Y"), source="kpi_monthly.csv")
    a.put("worst_sla_month_log", LOG_LABELS[worst.log], source="kpi_monthly.csv")
    a.put("worst_sla_month_rate", float(worst.sla_breach_rate), "pct", "kpi_monthly.csv")

    # year comparison (2017 = two departments, 2018 = whole university)
    yr = con.execute(f"SELECT year(submit_month) AS year, log, {KPI_SELECT} {base} GROUP BY ALL ORDER BY 2,1").df()
    yr.to_csv(d / "kpi_by_year.csv", index=False)
    arts.append("outputs/kpis/kpi_by_year.csv")
    for _, r in yr.iterrows():
        a.put(f"{r.log}_{r.year}_claims", int(r.claims), "int", "kpi_by_year.csv")
        a.put(f"{r.log}_{r.year}_median_cycle_days", float(r.median_cycle_days), "d1", "kpi_by_year.csv")
        a.put(f"{r.log}_{r.year}_sla_breach_rate", float(r.sla_breach_rate), "pct", "kpi_by_year.csv")

    # SLA sensitivity
    rows = []
    for t in SLA_SENSITIVITY:
        for log, rate in con.execute(f"""SELECT log, avg((cycle_days > {t})::INT) {base} AND outcome='PAID'
                                        GROUP BY 1 ORDER BY 1""").fetchall():
            rows.append({"sla_days": t, "log": log, "breach_rate": rate})
            a.put(f"sla{t}_{log}_breach_rate", rate, "pct", "sla_sensitivity.csv")

    pd.DataFrame(rows).to_csv(d / "sla_sensitivity.csv", index=False)
    arts.append("outputs/kpis/sla_sensitivity.csv")

    # rejections by reviewing role
    role = con.execute("""
      SELECT e.log, e.actor AS role, count(*) AS rejections
      FROM silver.event e JOIN gold.claim c USING (log, case_id)
      WHERE e.doc_type='CLAIM' AND e.action='REJECTED' AND e.actor <> 'EMPLOYEE' AND NOT c.censored
      GROUP BY ALL ORDER BY 1, 3 DESC""").df()
    role["share_of_log_rejections"] = role.rejections / role.groupby("log").rejections.transform("sum")
    role.to_csv(d / "rejections_by_role.csv", index=False)
    arts.append("outputs/kpis/rejections_by_role.csv")
    tot = role.groupby("role").rejections.sum().sort_values(ascending=False)
    a.put("org_rejection_events", int(tot.sum()), "int", "rejections_by_role.csv")
    a.put("top_rejecting_role", tot.index[0].title(), source="rejections_by_role.csv")
    a.put("top_rejecting_role_share", float(tot.iloc[0] / tot.sum()), "pct", "rejections_by_role.csv")

    # waiting time into each reviewing role's decision
    wait = con.execute("""
      SELECT log, regexp_extract(to_step, ' by (.+)$', 1) AS deciding_role, count(*) AS n,
             median(wait_hours)/24 AS median_wait_days, quantile_cont(wait_hours, 0.9)/24 AS p90_wait_days
      FROM gold.transition t JOIN gold.claim c USING (log, case_id)
      WHERE NOT c.censored AND regexp_matches(to_step, '^(APPROVED|FINAL_APPROVED|REJECTED) by ')
        AND regexp_extract(to_step, ' by (.+)$', 1) <> 'EMPLOYEE'
      GROUP BY ALL HAVING count(*) >= 30 ORDER BY 1, 4 DESC""").df()
    wait.to_csv(d / "wait_by_role.csv", index=False)
    arts.append("outputs/kpis/wait_by_role.csv")

    # organisational unit (only ID via the permit, and RFP, carry it)
    org = con.execute(f"""SELECT log, org_unit, {KPI_SELECT} {base} AND org_unit IS NOT NULL AND org_unit <> 'UNKNOWN'
                          GROUP BY ALL HAVING count(*) >= {ORG_MIN} ORDER BY claims DESC""").df()
    org.to_csv(d / "kpi_by_org_unit.csv", index=False)
    arts.append("outputs/kpis/kpi_by_org_unit.csv")
    a.put("org_units_ge50", len(org), "int", "kpi_by_org_unit.csv")
    a.put("org_unit_rej_min", float(org.org_rejection_rate.min()), "pct", "kpi_by_org_unit.csv")
    a.put("org_unit_rej_max", float(org.org_rejection_rate.max()), "pct", "kpi_by_org_unit.csv")
    a.put("org_unit_sla_min", float(org.sla_breach_rate.min()), "pct", "kpi_by_org_unit.csv")
    a.put("org_unit_sla_max", float(org.sla_breach_rate.max()), "pct", "kpi_by_org_unit.csv")

    # budget number for domestic (no org unit in that log)
    bud = con.execute(f"""SELECT log, budget_number, {KPI_SELECT} {base} AND log='DD'
                          GROUP BY ALL HAVING count(*) >= {ORG_MIN} ORDER BY claims DESC""").df()
    bud.to_csv(d / "kpi_dd_by_budget.csv", index=False)
    arts.append("outputs/kpis/kpi_dd_by_budget.csv")
    a.put("dd_budgets_ge50", len(bud), "int", "kpi_dd_by_budget.csv")

    # effect of extra approval layers and rework on cycle time
    lay = con.execute(f"""SELECT director_step, budget_owner_step, rejected_any, count(*) AS claims,
                            median(cycle_days) AS median_cycle_days {base} AND outcome='PAID'
                          GROUP BY ALL ORDER BY ALL""").df()
    lay.to_csv(d / "cycle_by_path_features.csv", index=False)
    arts.append("outputs/kpis/cycle_by_path_features.csv")
    for flag in ["rejected_any", "budget_owner_step", "director_step"]:
        w, wo = con.execute(f"""SELECT median(CASE WHEN {flag}::INT=1 THEN cycle_days END),
                                       median(CASE WHEN {flag}::INT=0 THEN cycle_days END)
                                {base} AND outcome='PAID'""").fetchone()
        a.put(f"median_cycle_with_{flag}", w, "d1", "gold.claim")
        a.put(f"median_cycle_without_{flag}", wo, "d1", "gold.claim")

    # ---- figures -----------------------------------------------------------
    fig, ax = plt.subplots(figsize=(9, 3.6))
    for log, g in monthly.groupby("log"):
        ax.plot(g.submit_month, g.claims, color=LOG_COLOURS[log], label=LOG_LABELS[log])
    ax.axvline(np.datetime64("2018-01-01"), color=INK2, lw=1, ls=":")
    ax.text(np.datetime64("2018-01-10"), ax.get_ylim()[1] * 0.92, "2018: whole university in scope", color=INK2, fontsize=9)
    ax.set_title("Claims submitted per month")
    ax.set_ylabel("claims")
    ax.legend(loc="upper left")
    arts.append(save(fig, FIG_DIR / "kpi_monthly_volume.png"))

    fig, ax = plt.subplots(figsize=(9, 3.6))
    for log, g in monthly.groupby("log"):
        ax.plot(g.submit_month, g.sla_breach_rate * 100, color=LOG_COLOURS[log], label=LOG_LABELS[log])
    ax.set_title(f"Paid claims breaching the {SLA_DAYS}-day analytical SLA, by submission month")
    ax.set_ylabel("% of paid claims")
    ax.legend(loc="upper left")
    arts.append(save(fig, FIG_DIR / "kpi_monthly_sla_breach.png"))

    cyc = con.execute(f"SELECT log, cycle_days {base} AND outcome='PAID'").df()
    fig, ax = plt.subplots(figsize=(9, 3.6))
    for log, g in cyc.groupby("log"):
        x = np.sort(g.cycle_days.values)
        ax.plot(x, np.arange(1, len(x) + 1) / len(x) * 100, color=LOG_COLOURS[log], label=LOG_LABELS[log])
    ax.axvline(SLA_DAYS, color=INK2, lw=1, ls=":")
    ax.text(SLA_DAYS + 0.5, 8, f"{SLA_DAYS}-day SLA", color=INK2, fontsize=9)
    ax.set_xlim(0, 60)
    ax.set_title("Cumulative share of paid claims by cycle time (submission to payment)")
    ax.set_xlabel("calendar days")
    ax.set_ylabel("% of paid claims")
    ax.legend(loc="lower right")
    arts.append(save(fig, FIG_DIR / "kpi_cycle_ecdf.png"))

    fig, ax = plt.subplots(figsize=(7, 3.2))
    t = tot.sort_values()
    ax.barh([r.title() for r in t.index], t.values, color=SEQ_BLUE, height=0.6)
    for i, v in enumerate(t.values):
        ax.text(v, i, f" {v:,}", va="center", color=INK2, fontsize=9)
    ax.set_title("Claim rejections by reviewing role (all three logs)")
    ax.set_xlabel("rejection events")
    ax.grid(axis="y", visible=False)
    arts.append(save(fig, FIG_DIR / "kpi_rejections_by_role.png"))

    con.close()
    a.save([x if x.startswith("outputs") else "outputs/" + x.split("/outputs/")[1] for x in arts])
