"""Step 3: process mining. Discovery, variant analysis, bottlenecks (waiting time
between steps, payment batching) and conformance checking against the intended
approval process published with the dataset."""
from __future__ import annotations

import duckdb
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pm4py
from pm4py.objects.process_tree.obj import Operator, ProcessTree

from .audit import Audit
from .config import DECLARE_WINDOW_DAYS, FIG_DIR, OUT, WAREHOUSE
from .viz import INK2, LOG_COLOURS, LOG_LABELS, SEQ_BLUE, save, style

D = OUT / "mining"

SUB, ADM, BO = "SUBMITTED by EMPLOYEE", "APPROVED by ADMINISTRATION", "APPROVED by BUDGET OWNER"
SUP_F, SUP_A, DIR_F = "FINAL_APPROVED by SUPERVISOR", "APPROVED by SUPERVISOR", "FINAL_APPROVED by DIRECTOR"
REQ, PAY = "Request Payment", "Payment Handled"


def _leaf(label, parent):
    return ProcessTree(label=label, parent=parent)


def reference_tree() -> ProcessTree:
    """Intended process from the dataset description:
    submit -> travel administration -> [budget owner] -> supervisor final
    (or supervisor then director final) -> request payment -> payment handled.
    The budget-owner step is optional because, when the budget owner and the
    supervisor are the same person, only one of the two steps is taken."""
    root = ProcessTree(operator=Operator.SEQUENCE)
    kids = [_leaf(SUB, root), _leaf(ADM, root)]
    opt_bo = ProcessTree(operator=Operator.XOR, parent=root)
    opt_bo.children = [_leaf(BO, opt_bo), _leaf(None, opt_bo)]
    final = ProcessTree(operator=Operator.XOR, parent=root)
    sup_dir = ProcessTree(operator=Operator.SEQUENCE, parent=final)
    sup_dir.children = [_leaf(SUP_A, sup_dir), _leaf(DIR_F, sup_dir)]
    final.children = [_leaf(SUP_F, final), sup_dir]
    kids += [opt_bo, final, _leaf(REQ, root), _leaf(PAY, root)]
    root.children = kids
    return root


def _event_log(con) -> pd.DataFrame:
    df = con.execute("""
      SELECT e.log, e.log || '|' || e.case_id AS case_key, e.step, e.ts_utc, e.event_seq
      FROM silver.event e JOIN gold.claim c USING (log, case_id)
      WHERE e.doc_type IN ('CLAIM','PAYMENT') AND NOT c.censored
      ORDER BY e.log, case_key, e.ts_utc, e.event_seq""").df()
    df["ts_utc"] = pd.to_datetime(df.ts_utc, utc=True)
    return pm4py.format_dataframe(df, case_id="case_key", activity_key="step", timestamp_key="ts_utc")


def run():
    style()
    a = Audit("step3_mining")
    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    arts = []
    D.mkdir(parents=True, exist_ok=True)
    full = _event_log(con)

    # ---- variants ----------------------------------------------------------
    var_rows = []
    for log, df in full.groupby("log"):
        seqs = df.groupby("case:concept:name")["concept:name"].agg(" > ".join)
        vc = seqs.value_counts()
        n = len(seqs)
        a.put(f"{log}_variants", len(vc), "int", "variants.csv")
        a.put(f"{log}_top1_variant_share", vc.iloc[0] / n, "pct", "variants.csv")
        a.put(f"{log}_top5_variant_share", vc.iloc[:5].sum() / n, "pct", "variants.csv")
        a.put(f"{log}_variants_for_90pct", int((vc.cumsum() / n < 0.9).sum() + 1), "int", "variants.csv")
        a.put(f"{log}_singleton_variants", int((vc == 1).sum()), "int", "variants.csv")
        for rank, (v, c) in enumerate(vc.items(), 1):
            var_rows.append({"log": log, "rank": rank, "cases": c, "share": c / n, "cum_share": vc.iloc[:rank].sum() / n,
                             "n_steps": v.count(" > ") + 1, "variant": v})
    var = pd.DataFrame(var_rows)
    var.to_csv(D / "variants.csv", index=False)
    arts.append("outputs/mining/variants.csv")
    a.put("DD_top1_variant", var[(var.log == "DD") & (var["rank"] == 1)].variant.iloc[0], source="variants.csv")

    fig, ax = plt.subplots(figsize=(8, 3.4))
    for log, g in var.groupby("log"):
        g = g[g["rank"] <= 30]
        ax.plot(g["rank"], g.cum_share * 100, color=LOG_COLOURS[log], marker="o", ms=3, label=LOG_LABELS[log])
    ax.set_title("Variant coverage: cumulative share of claims by top-N variants")
    ax.set_xlabel("number of variants (most frequent first)")
    ax.set_ylabel("% of claims covered")
    ax.legend(loc="lower right")
    arts.append(save(fig, FIG_DIR / "pm_variant_coverage.png"))

    # ---- discovery (inductive miner, per log) -------------------------------
    for log, df in full.groupby("log"):
        net, im, fm = pm4py.discover_petri_net_inductive(df, noise_threshold=0.2)
        fit = pm4py.fitness_token_based_replay(df, net, im, fm)
        prec = pm4py.precision_token_based_replay(df, net, im, fm)
        a.put(f"{log}_discovered_fitness", fit["log_fitness"], "d3", "inductive miner, token replay")
        a.put(f"{log}_discovered_precision", prec, "d3", "inductive miner, token replay")
        pm4py.write_pnml(net, im, fm, str(D / f"discovered_{log}.pnml"))
        pm4py.save_vis_petri_net(net, im, fm, str(FIG_DIR / f"pm_discovered_{log}.png"), rankdir="LR")
        arts += [f"outputs/mining/discovered_{log}.pnml", f"outputs/figures/pm_discovered_{log}.png"]
    dfg, sa, ea = pm4py.discover_dfg(full[full.log == "DD"])
    pm4py.save_vis_dfg(dfg, sa, ea, str(FIG_DIR / "pm_dfg_DD.png"), rankdir="LR")
    arts.append("outputs/figures/pm_dfg_DD.png")

    # ---- bottlenecks -------------------------------------------------------
    tr = con.execute("""
      SELECT t.log, from_step, to_step, count(*) AS n, median(wait_hours)/24 AS median_wait_days,
             quantile_cont(wait_hours,0.9)/24 AS p90_wait_days, sum(wait_hours)/24 AS total_wait_days
      FROM gold.transition t JOIN gold.claim c USING (log, case_id) WHERE NOT c.censored
      GROUP BY ALL HAVING count(*) >= 50 ORDER BY total_wait_days DESC""").df()
    tr.to_csv(D / "transition_waits.csv", index=False)
    arts.append("outputs/mining/transition_waits.csv")
    # denominator: waiting time over ALL hand-offs, not only the frequent ones listed in the CSV
    tot_wait = con.execute("""SELECT sum(wait_hours)/24 FROM gold.transition t JOIN gold.claim c USING (log, case_id)
                              WHERE NOT c.censored""").fetchone()[0]
    top = tr.iloc[0]
    a.put("top_wait_transition", f"{top.from_step} to {top.to_step}", source="transition_waits.csv")
    a.put("top_wait_transition_log", LOG_LABELS[top.log], source="transition_waits.csv")
    a.put("top_wait_transition_share", top.total_wait_days / tot_wait, "pct", "transition_waits.csv")
    pay = tr[tr.to_step == PAY]
    a.put("wait_share_into_payment_handled", pay.total_wait_days.sum() / tot_wait, "pct", "transition_waits.csv")
    req = tr[(tr.from_step.str.startswith("FINAL_APPROVED")) & (tr.to_step == REQ)]
    a.put("wait_share_final_to_request", req.total_wait_days.sum() / tot_wait, "pct", "transition_waits.csv")
    a.put("wait_share_post_approval", (req.total_wait_days.sum() + tr[(tr.from_step == REQ) & (tr.to_step == PAY)].total_wait_days.sum()) / tot_wait,
          "pct", "transition_waits.csv")
    for log in ["DD", "ID", "RFP"]:
        r = tr[(tr.log == log) & (tr.from_step == REQ) & (tr.to_step == PAY)]
        a.put(f"{log}_median_request_to_paid_days", float(r.median_wait_days.iloc[0]), "d1", "transition_waits.csv")
        r = tr[(tr.log == log) & (tr.from_step == SUP_F) & (tr.to_step == REQ)]
        a.put(f"{log}_median_final_to_request_days", float(r.median_wait_days.iloc[0]), "d1", "transition_waits.csv")

    # payment batching
    batch = con.execute("""
      SELECT dayname(ts_local) AS weekday, hour(ts_local) AS hour, count(*) AS payments
      FROM silver.event WHERE activity='Payment Handled' GROUP BY ALL ORDER BY payments DESC""").df()
    batch.to_csv(D / "payment_batch_profile.csv", index=False)
    arts.append("outputs/mining/payment_batch_profile.csv")
    tot_p = batch.payments.sum()
    top2 = batch.groupby("weekday").payments.sum().nlargest(2).index
    monthu = batch[batch.weekday.isin(top2)].payments.sum()
    at17 = batch[batch.hour == batch.groupby("hour").payments.sum().idxmax()].payments.sum()
    a.put("payments_total", int(tot_p), "int", "payment_batch_profile.csv")
    a.put("payments_top2_days_share", monthu / tot_p, "pct", "payment_batch_profile.csv")
    a.put("payments_peak_hour_share", at17 / tot_p, "pct", "payment_batch_profile.csv")
    wd = batch.groupby("weekday").payments.sum().sort_values(ascending=False)
    a.put("payments_top_days", f"{wd.index[0]}s and {wd.index[1]}s", source="payment_batch_profile.csv")
    a.put("payments_peak_hour", f"{int(batch.groupby('hour').payments.sum().idxmax()):02d}:00", source="payment_batch_profile.csv")
    a.put("declare_window_days", DECLARE_WINDOW_DAYS, "int", "config.DECLARE_WINDOW_DAYS")
    rp = con.execute("""SELECT dayname(ts_local) AS weekday, count(*) AS n FROM silver.event
                        WHERE activity='Request Payment' GROUP BY 1 ORDER BY 2 DESC""").df()
    rp.to_csv(D / "request_payment_weekday.csv", index=False)
    arts.append("outputs/mining/request_payment_weekday.csv")

    fig, ax = plt.subplots(figsize=(9, 3.6))
    t = tr.head(10).iloc[::-1]
    labels = [f"{LOG_LABELS[r.log].split()[0]}: {r.from_step} > {r.to_step}" for r in t.itertuples()]
    ax.barh(labels, t.total_wait_days / 1000, color=SEQ_BLUE, height=0.6)
    for i, r in enumerate(t.itertuples()):
        ax.text(r.total_wait_days / 1000, i, f"  median {r.median_wait_days:.1f} d, n={r.n:,}", va="center", fontsize=8, color=INK2)
    ax.set_title("Top 10 hand-offs by total waiting time")
    ax.set_xlabel("total waiting time (thousand claim-days)")
    ax.grid(axis="y", visible=False)
    ax.tick_params(axis="y", labelsize=8)
    arts.append(save(fig, FIG_DIR / "pm_bottlenecks.png"))

    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    wk = batch.groupby("weekday").payments.sum().reindex(days, fill_value=0)
    fig, ax = plt.subplots(figsize=(7, 3))
    ax.bar([d[:3] for d in days], wk.values, color=SEQ_BLUE, width=0.6)
    for i, v in enumerate(wk.values):
        ax.text(i, v, f"{v:,}", ha="center", va="bottom", fontsize=8, color=INK2)
    ax.set_title("Payment Handled events by weekday (batch payment runs)")
    ax.set_ylabel("payments")
    ax.grid(axis="x", visible=False)
    arts.append(save(fig, FIG_DIR / "pm_payment_weekday.png"))

    # ---- conformance against the intended process --------------------------
    tree = reference_tree()
    net, im, fm = pm4py.convert_to_petri_net(tree)
    pm4py.write_pnml(net, im, fm, str(D / "reference_model.pnml"))
    pm4py.save_vis_petri_net(net, im, fm, str(FIG_DIR / "pm_reference_model.png"), rankdir="LR")
    arts += ["outputs/mining/reference_model.pnml", "outputs/figures/pm_reference_model.png"]
    conf_rows = []
    for log, df in full.groupby("log"):
        elog = pm4py.convert_to_event_log(df)
        al = pm4py.conformance_diagnostics_alignments(elog, net, im, fm, multi_processing=False)
        cases = [t.attributes["concept:name"] for t in elog]  # same order as the alignments
        assert len(cases) == len(al)
        for cid, r in zip(cases, al):
            conf_rows.append({"log": log, "case_key": cid, "fitness": r["fitness"],
                              "cost": r["cost"], "fits": r["fitness"] == 1.0})
    conf = pd.DataFrame(conf_rows)
    conf.to_csv(D / "alignment_fitness_by_case.csv", index=False)
    arts.append("outputs/mining/alignment_fitness_by_case.csv")
    for log, g in conf.groupby("log"):
        a.put(f"{log}_conform_rate", g.fits.mean(), "pct", "alignment_fitness_by_case.csv")
        a.put(f"{log}_mean_alignment_fitness", g.fitness.mean(), "d3", "alignment_fitness_by_case.csv")
    a.put("ALL_conform_rate", conf.fits.mean(), "pct", "alignment_fitness_by_case.csv")

    # rule-based compliance checks (explain the deviations in business terms)
    rules = con.execute(f"""
    WITH c AS (SELECT * FROM gold.claim WHERE NOT censored),
    s AS (
      SELECT e.log, e.case_id,
        min(CASE WHEN step='{ADM}' THEN ts_utc END) AS adm_ts,
        min(CASE WHEN step='APPROVED by PRE_APPROVER' THEN ts_utc END) AS pre_ts,
        min(CASE WHEN step IN ('{SUP_F}','{DIR_F}') THEN ts_utc END) AS final_ts,
        min(CASE WHEN step='{REQ}' THEN ts_utc END) AS req_ts
      FROM silver.event e GROUP BY 1,2)
    SELECT c.log,
      count(*) AS claims,
      count(*) FILTER (WHERE c.outcome='PAID' AND s.final_ts IS NULL) AS r1_paid_without_final_approval,
      count(*) FILTER (WHERE c.outcome='PAID' AND s.adm_ts IS NULL AND s.pre_ts IS NULL) AS r2_paid_without_admin_check,
      count(*) FILTER (WHERE c.outcome='PAID' AND s.adm_ts IS NULL AND s.pre_ts IS NOT NULL) AS r2b_pre_approver_instead_of_admin,
      count(*) FILTER (WHERE s.adm_ts > s.final_ts) AS r3_admin_after_final_approval,
      count(*) FILTER (WHERE s.req_ts < s.final_ts) AS r4_payment_requested_before_final_approval,
      count(*) FILTER (WHERE c.log='ID' AND c.permit_final_ts IS NULL) AS r5_no_approved_permit,
      count(*) FILTER (WHERE c.log='ID' AND c.permit_final_ts > c.trip_start_ts) AS r6_permit_approved_after_trip_start,
      count(*) FILTER (WHERE c.log='ID' AND c.first_submit_ts > c.trip_end_ts + INTERVAL {DECLARE_WINDOW_DAYS} DAY) AS r7_declared_late_after_trip,
      count(*) FILTER (WHERE c.n_submissions > 1) AS r8_resubmitted
    FROM c JOIN s USING (log, case_id) GROUP BY 1 ORDER BY 1""").df()
    rules.to_csv(D / "compliance_rules.csv", index=False)
    arts.append("outputs/mining/compliance_rules.csv")
    for _, r in rules.iterrows():
        for col in rules.columns[2:]:
            a.put(f"{r.log}_{col.split('_')[0]}", int(r[col]), "int", "compliance_rules.csv")
            a.put(f"{r.log}_{col.split('_')[0]}_rate", r[col] / r.claims, "pct", "compliance_rules.csv")

    con.close()
    a.save([x if x.startswith("outputs") else "outputs/" + x.split("/outputs/")[1] for x in arts])
