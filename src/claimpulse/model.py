"""Step 4: early-warning model. At the moment a claim is first submitted, score
the risk that it becomes a problem claim (rejected by a reviewer, not paid, or
paid later than the analytical SLA). Explain it with SHAP and turn the score into
a RED / AMBER / GREEN monitoring view.

Leakage controls:
- every feature is known at first submission: claim attributes, calendar, trip
  dates from the permit, permit events dated before submission, and history of
  the same org unit / budget built only from claims CLOSED before submission;
- temporal split: train < MODEL_VALID_START <= validation < MODEL_TEST_START <= test;
- tuning, calibration and RAG cut-offs use validation only; test is touched once.
"""
from __future__ import annotations

import json

import duckdb
import lightgbm as lgb
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import shap
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .audit import Audit
from .config import (FIG_DIR, HISTORY_SMOOTHING, HISTORY_WINDOW_DAYS, MODEL_TEST_START, MODEL_VALID_START, OUT,
                     RAG_AMBER_TOP, RAG_RED_TOP, RANDOM_STATE, WAREHOUSE, CI_LEVEL, BOOTSTRAP_RESAMPLES)
from .viz import INK2, LOG_LABELS, STATUS, save, style

D = OUT / "model"

FEATURES = {
    "is_DD": "Domestic declaration", "is_ID": "International declaration", "is_RFP": "Request for payment",
    "log_amount": "Claim amount (log)", "amount_zero": "Zero amount",
    "submit_hour": "Submission hour", "submit_weekday": "Submission weekday", "submit_month": "Submission month",
    "submit_dom": "Submission day of month", "workload_7d": "Claims submitted in prior 7 days (same log)",
    "org_prior_n": "Org unit: claims closed in prior 180 days (log)", "org_prior_rate": "Org unit: problem rate, prior 180 days",
    "bud_prior_n": "Budget: claims closed in prior 180 days (log)", "bud_prior_rate": "Budget: problem rate, prior 180 days",
    "id_days_trip_end_to_submit": "Days from trip end to submission", "id_trip_days": "Trip length (days)",
    "id_permit_approved": "Permit final-approved before submission",
    "id_permit_after_trip_start": "Permit approved after trip start",
    "id_permit_rejections": "Permit rejections before submission",
    "id_amount_to_permit_budget": "Amount / permit requested budget", "rfp_cost_type": "Cost type",
}


def build_features(con, base_rate: float | None = None) -> pd.DataFrame:
    df = con.execute(f"""
    WITH c AS (
      SELECT g.*, l.close_ts FROM gold.claim g
      JOIN (SELECT log, case_id, max(ts_utc) AS close_ts FROM silver.event
            WHERE doc_type IN ('CLAIM','PAYMENT') GROUP BY 1,2) l USING (log, case_id)
      WHERE NOT g.censored),
    permit AS (
      SELECT c.log, c.case_id,
        min(CASE WHEN e.doc_type='PERMIT' AND e.action='FINAL_APPROVED' THEN e.ts_utc END) AS permit_final_ts,
        count(CASE WHEN e.doc_type='PERMIT' AND e.action='REJECTED' AND e.actor<>'EMPLOYEE' THEN 1 END) AS permit_rej
      FROM c JOIN silver.event e USING (log, case_id)
      WHERE e.ts_utc < c.first_submit_ts GROUP BY 1,2),
    org_hist AS (
      SELECT a.log, a.case_id, count(b.case_id) AS n, sum(b.problem_claim::INT) AS p
      FROM c a JOIN c b ON b.org_unit = a.org_unit AND b.close_ts < a.first_submit_ts
                        AND b.close_ts >= a.first_submit_ts - INTERVAL {HISTORY_WINDOW_DAYS} DAY
      WHERE a.org_unit IS NOT NULL AND a.org_unit <> 'UNKNOWN' GROUP BY 1,2),
    bud_hist AS (
      SELECT a.log, a.case_id, count(b.case_id) AS n, sum(b.problem_claim::INT) AS p
      FROM c a JOIN c b ON b.budget_number = a.budget_number AND b.close_ts < a.first_submit_ts
                        AND b.close_ts >= a.first_submit_ts - INTERVAL {HISTORY_WINDOW_DAYS} DAY
      WHERE a.budget_number IS NOT NULL AND a.budget_number <> 'UNKNOWN' GROUP BY 1,2),
    load AS (
      SELECT log, case_id, count(*) OVER (PARTITION BY log ORDER BY first_submit_ts
             RANGE BETWEEN INTERVAL 7 DAY PRECEDING AND CURRENT ROW) - 1 AS workload_7d FROM c)
    SELECT c.log, c.case_id, c.first_submit_ts, c.submit_month AS submit_period,
      c.problem_claim::INT AS y, c.rejected_any::INT AS y_rejected, c.sla_breach::INT AS y_sla,
      (c.outcome <> 'PAID')::INT AS y_unpaid,
      (c.log='DD')::INT AS is_DD, (c.log='ID')::INT AS is_ID, (c.log='RFP')::INT AS is_RFP,
      ln(1 + c.amount) AS log_amount, (c.amount = 0)::INT AS amount_zero,
      hour(c.first_submit_local) AS submit_hour, isodow(c.first_submit_local) AS submit_weekday,
      month(c.first_submit_local) AS submit_month, day(c.first_submit_local) AS submit_dom,
      ld.workload_7d,
      oh.n AS org_n, oh.p AS org_p, bh.n AS bud_n, bh.p AS bud_p,
      CASE WHEN c.log='ID' THEN date_diff('second', c.trip_end_ts, c.first_submit_ts)/86400.0 END AS id_days_trip_end_to_submit,
      CASE WHEN c.log='ID' THEN date_diff('second', c.trip_start_ts, c.trip_end_ts)/86400.0 END AS id_trip_days,
      CASE WHEN c.log='ID' THEN (pm.permit_final_ts IS NOT NULL)::INT END AS id_permit_approved,
      CASE WHEN c.log='ID' THEN coalesce((pm.permit_final_ts > c.trip_start_ts)::INT, 0) END AS id_permit_after_trip_start,
      CASE WHEN c.log='ID' THEN coalesce(pm.permit_rej, 0) END AS id_permit_rejections,
      CASE WHEN c.log='ID' AND c.permit_requested_budget > 0 THEN c.amount / c.permit_requested_budget END AS id_amount_to_permit_budget,
      CASE WHEN c.log='RFP' THEN c.cost_type END AS rfp_cost_type
    FROM c
    LEFT JOIN permit pm USING (log, case_id)
    LEFT JOIN org_hist oh USING (log, case_id)
    LEFT JOIN bud_hist bh USING (log, case_id)
    LEFT JOIN load ld USING (log, case_id)
    ORDER BY c.first_submit_ts, c.log, c.case_id""").df()
    df["first_submit_ts"] = pd.to_datetime(df.first_submit_ts, utc=True)
    df["split"] = np.where(df.first_submit_ts < pd.Timestamp(MODEL_VALID_START, tz="UTC"), "train",
                           np.where(df.first_submit_ts < pd.Timestamp(MODEL_TEST_START, tz="UTC"), "valid", "test"))
    if base_rate is None:
        base_rate = df.loc[df.split == "train", "y"].mean()
    m = HISTORY_SMOOTHING
    # Shrunk prior rate; a claim with no closed history gets the training base rate.
    for k in ["org", "bud"]:
        n, p = df[f"{k}_n"].fillna(0), df[f"{k}_p"].fillna(0)
        df[f"{k}_prior_rate"] = (p + m * base_rate) / (n + m)
        df[f"{k}_prior_n"] = np.log1p(n)
    # The attribute does not exist in that log (DD has no org unit, RFP no budget number): leave missing.
    df.loc[df.log == "DD", ["org_prior_rate", "org_prior_n"]] = np.nan
    df.loc[df.log == "RFP", ["bud_prior_rate", "bud_prior_n"]] = np.nan
    df.attrs["base_rate"] = base_rate
    return df


def bootstrap_auc(y, s, n=BOOTSTRAP_RESAMPLES, seed=RANDOM_STATE):
    rng = np.random.default_rng(seed)
    y, s = np.asarray(y), np.asarray(s)
    out = []
    for _ in range(n):
        i = rng.integers(0, len(y), len(y))
        if y[i].min() != y[i].max():
            out.append(roc_auc_score(y[i], s[i]))
    tail = (1 - CI_LEVEL) / 2 * 100
    return np.percentile(out, [tail, 100 - tail])


def run():
    style()
    a = Audit("step4_model")
    D.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    df = build_features(con)
    con.close()
    X = df[list(FEATURES)].astype(float)
    tr, va, te = (df.split == s for s in ["train", "valid", "test"])
    a.put("model_base_rate_train", float(df.y[tr].mean()), "pct", "model features")
    for name, msk in [("train", tr), ("valid", va), ("test", te)]:
        a.put(f"model_n_{name}", int(msk.sum()), "int", "model features")
        a.put(f"model_pos_rate_{name}", float(df.y[msk].mean()), "pct", "model features")
    a.put("model_valid_start", pd.Timestamp(MODEL_VALID_START).strftime("%d %B %Y"), source="config")
    a.put("model_test_start", pd.Timestamp(MODEL_TEST_START).strftime("%d %B %Y"), source="config")
    a.put("model_n_features", len(FEATURES), "int", "model.FEATURES")
    a.put("model_ci_level", CI_LEVEL, "pct", "config.CI_LEVEL")
    a.put("model_bootstrap_resamples", BOOTSTRAP_RESAMPLES, "int", "config.BOOTSTRAP_RESAMPLES")
    a.put("rag_red_top_pct", RAG_RED_TOP, "pct", "config.RAG_RED_TOP")
    a.put("rag_amber_band_pct", RAG_AMBER_TOP - RAG_RED_TOP, "pct", "config.RAG_AMBER_TOP")
    a.put("history_window_days", HISTORY_WINDOW_DAYS, "int", "config.HISTORY_WINDOW_DAYS")
    a.put("history_smoothing", HISTORY_SMOOTHING, "int", "config.HISTORY_SMOOTHING")
    a.put("model_feature_list", "; ".join(FEATURES.values()), source="model.FEATURES")

    # ---- small, validation-only hyper-parameter search ---------------------
    # Fixed grid, no early stopping, selected on validation AUC only.
    grid = [dict(num_leaves=nl, min_child_samples=mc, n_estimators=ne, learning_rate=0.03,
                 subsample=0.8, subsample_freq=1, colsample_bytree=0.8, reg_lambda=1.0,
                 random_state=RANDOM_STATE, verbose=-1, deterministic=True, force_row_wise=True, n_jobs=1)
            for nl in (7, 15, 31) for mc in (20, 50, 100) for ne in (100, 200, 400)]
    best = None
    for params in grid:
        m = lgb.LGBMClassifier(**params).fit(X[tr], df.y[tr])
        auc = roc_auc_score(df.y[va], m.predict_proba(X[va])[:, 1])
        if best is None or auc > best[0]:
            best = (auc, params, m)
    auc_va, params, gbm = best
    a.put("model_grid_size", len(grid), "int", "validation search")
    a.put("model_best_min_child", params["min_child_samples"], "int", "validation search")
    a.put("model_valid_auc", auc_va, "d3", "validation search")
    a.put("model_best_num_leaves", params["num_leaves"], "int", "validation search")
    a.put("model_best_trees", params["n_estimators"], "int", "validation search")
    raw_va = gbm.predict_proba(X[va])[:, 1]
    iso = IsotonicRegression(out_of_bounds="clip").fit(raw_va, df.y[va])
    raw_te = gbm.predict_proba(X[te])[:, 1]
    p_te = iso.predict(raw_te)
    p_va = iso.predict(raw_va)

    # baselines
    lr = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000))
    Xf = X.fillna(X[tr].median()).fillna(0)
    lr.fit(Xf[tr], df.y[tr])
    s_lr = lr.predict_proba(Xf[te])[:, 1]
    log_rate = df[tr].groupby("log").y.mean()
    s_logonly = df.loc[te, "log"].map(log_rate).values

    y = df.y[te].values
    ci = bootstrap_auc(y, raw_te)
    res = {
        "test_auc": roc_auc_score(y, raw_te), "test_auc_ci_low": ci[0], "test_auc_ci_high": ci[1],
        "test_pr_auc": average_precision_score(y, raw_te),
        "test_brier_calibrated": brier_score_loss(y, p_te),
        "test_brier_base_rate": brier_score_loss(y, np.full_like(p_te, df.y[tr].mean(), dtype=float)),
        "test_auc_logistic": roc_auc_score(y, s_lr), "test_auc_log_type_only": roc_auc_score(y, s_logonly),
        "test_auc_vs_rejected": roc_auc_score(df.y_rejected[te], raw_te),
        "test_auc_vs_unpaid": roc_auc_score(df.y_unpaid[te], raw_te),
    }
    paid_te = te & (df.y_unpaid == 0)
    res["test_auc_vs_sla_paid"] = roc_auc_score(df.y_sla[paid_te], gbm.predict_proba(X[paid_te])[:, 1])
    for log in ["DD", "ID", "RFP"]:
        msk = (df.log[te] == log).values
        res[f"test_auc_{log}"] = roc_auc_score(y[msk], raw_te[msk])
    order = np.argsort(-raw_te)
    top = order[: int(len(order) * 0.1)]
    res["test_top_decile_rate"] = y[top].mean()
    res["test_top_decile_lift"] = y[top].mean() / y.mean()
    for k, v in res.items():
        a.put(f"model_{k}", float(v), "pct" if k == "test_top_decile_rate" else ("d2" if "lift" in k else "d3"), "model_metrics.json")
    (D / "model_metrics.json").write_text(json.dumps({k: float(v) for k, v in res.items()} | {"params": params}, indent=2))

    # calibration table (test)
    cal = pd.DataFrame({"p": p_te, "y": y})
    cal["bin"] = pd.qcut(cal.p.rank(method="first"), 10, labels=False)
    cal = cal.groupby("bin").agg(mean_predicted=("p", "mean"), observed=("y", "mean"), n=("y", "size")).reset_index()
    cal.to_csv(D / "calibration_test.csv", index=False)

    # ---- RAG ---------------------------------------------------------------
    # cut-offs on the raw (uncalibrated, tie-free) score, fixed on validation
    red_cut = float(np.quantile(raw_va, 1 - RAG_RED_TOP))
    amber_cut = float(np.quantile(raw_va, 1 - RAG_AMBER_TOP))
    a.put("rag_red_cut", red_cut, "d3", "validation quantile of raw score")
    a.put("rag_amber_cut", amber_cut, "d3", "validation quantile of raw score")
    band = np.where(raw_te >= red_cut, "RED", np.where(raw_te >= amber_cut, "AMBER", "GREEN"))

    # ---- SHAP --------------------------------------------------------------
    expl = shap.TreeExplainer(gbm)
    sv = expl.shap_values(X[te])
    sv = sv[1] if isinstance(sv, list) else sv
    imp = pd.Series(np.abs(sv).mean(0), index=list(FEATURES)).sort_values(ascending=False)
    imp_df = imp.rename("mean_abs_shap").reset_index().rename(columns={"index": "feature"})
    imp_df["label"] = imp_df.feature.map(FEATURES)
    imp_df.to_csv(D / "shap_importance.csv", index=False)
    for i in range(3):
        a.put(f"shap_top{i + 1}", FEATURES[imp.index[i]], source="shap_importance.csv")
    names = np.array([FEATURES[f] for f in FEATURES])
    reasons = []
    for row in sv:
        idx = np.argsort(-row)[:3]
        reasons.append("; ".join(f"{names[j]} (+{row[j]:.2f})" for j in idx if row[j] > 0))

    mon = df.loc[te, ["log", "case_id", "first_submit_ts", "submit_period", "y", "y_rejected", "y_sla", "y_unpaid"]].copy()
    mon["risk_score"] = p_te
    mon["rag"] = band
    mon["top_risk_drivers"] = reasons
    mon["first_submit_ts"] = mon.first_submit_ts.dt.strftime("%Y-%m-%d %H:%M")
    mon.to_csv(D / "rag_monitor_claims.csv", index=False)
    bands = mon.groupby("rag").agg(claims=("y", "size"), problem_rate=("y", "mean"),
                                   rejected_rate=("y_rejected", "mean")).reindex(["RED", "AMBER", "GREEN"]).reset_index()
    bands["share_of_claims"] = bands.claims / bands.claims.sum()
    bands["share_of_problems_captured"] = (bands.claims * bands.problem_rate) / mon.y.sum()
    bands.to_csv(D / "rag_band_summary.csv", index=False)
    for r in bands.itertuples():
        a.put(f"rag_{r.rag}_claims", int(r.claims), "int", "rag_band_summary.csv")
        a.put(f"rag_{r.rag}_share", r.share_of_claims, "pct", "rag_band_summary.csv")
        a.put(f"rag_{r.rag}_problem_rate", r.problem_rate, "pct", "rag_band_summary.csv")
        a.put(f"rag_{r.rag}_capture", r.share_of_problems_captured, "pct", "rag_band_summary.csv")
    a.put("rag_red_amber_capture", float(bands.loc[bands.rag != "GREEN", "share_of_problems_captured"].sum()), "pct",
          "rag_band_summary.csv")
    monthly = mon.groupby(["submit_period", "rag"]).agg(claims=("y", "size"), problem_rate=("y", "mean")).reset_index()
    monthly.to_csv(D / "rag_monthly.csv", index=False)

    # ---- figures -----------------------------------------------------------
    fig, ax = plt.subplots(figsize=(7, 4))
    t = imp_df.head(12).iloc[::-1]
    ax.barh(t.label, t.mean_abs_shap, color="#3987e5", height=0.6)
    ax.set_title("What drives predicted risk (mean |SHAP|, test set)")
    ax.set_xlabel("mean absolute SHAP value (log-odds)")
    ax.grid(axis="y", visible=False)
    save(fig, FIG_DIR / "model_shap_importance.png")

    shap.summary_plot(sv, X[te].rename(columns=FEATURES), show=False, max_display=12, plot_size=(8, 5))
    fig = plt.gcf()
    fig.axes[0].set_title("SHAP values per claim (test set)", loc="left", fontweight="bold")
    save(fig, FIG_DIR / "model_shap_beeswarm.png")

    fig, ax = plt.subplots(figsize=(6.5, 3.4))
    xs = np.arange(3)
    ax.bar(xs, bands.problem_rate * 100, color=[STATUS[b] for b in bands.rag], width=0.55)
    for i, r in enumerate(bands.itertuples()):
        ax.text(i, r.problem_rate * 100, f"{r.problem_rate * 100:.1f}%\n{r.claims:,} claims", ha="center", va="bottom",
                fontsize=9, color=INK2)
    ax.set_xticks(xs, [f"{b}" for b in bands.rag])
    ax.axhline(mon.y.mean() * 100, color=INK2, lw=1, ls=":")
    ax.text(-0.45, mon.y.mean() * 100, "test base rate", va="bottom", ha="left", fontsize=8, color=INK2)
    ax.set_ylim(0, max(bands.problem_rate) * 130)
    ax.set_title("Observed problem-claim rate by RAG band (unseen test period)")
    ax.set_ylabel("% problem claims")
    ax.grid(axis="x", visible=False)
    save(fig, FIG_DIR / "model_rag_bands.png")

    fig, ax = plt.subplots(figsize=(5, 4))
    ax.plot([0, 100], [0, 100], color=INK2, lw=1, ls=":")
    ax.plot(cal.mean_predicted * 100, cal.observed * 100, marker="o", color="#2a78d6")
    ax.set_title("Calibration on test (deciles)")
    ax.set_xlabel("mean predicted risk (%)")
    ax.set_ylabel("observed problem rate (%)")
    save(fig, FIG_DIR / "model_calibration.png")

    a.save(["outputs/model/model_metrics.json", "outputs/model/calibration_test.csv", "outputs/model/shap_importance.csv",
            "outputs/model/rag_monitor_claims.csv", "outputs/model/rag_band_summary.csv", "outputs/model/rag_monthly.csv",
            "outputs/figures/model_shap_importance.png", "outputs/figures/model_shap_beeswarm.png",
            "outputs/figures/model_rag_bands.png", "outputs/figures/model_calibration.png"])
