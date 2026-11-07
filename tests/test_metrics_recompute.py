"""Independent recomputation of audited KPIs from the shipped claim-level outputs."""
import pandas as pd
import pytest
from conftest import ROOT, mv

from claimpulse.config import SLA_DAYS

FACT = ROOT / "outputs" / "powerbi" / "fact_claim.csv"


@pytest.fixture(scope="module")
def fact():
    return pd.read_csv(FACT)


@pytest.mark.parametrize("log", ["DD", "ID", "RFP", "ALL"])
def test_headline_kpis(fact, metrics, log):
    f = fact if log == "ALL" else fact[fact.log_code == log]
    paid = f[f.outcome == "PAID"]
    assert len(f) == mv(metrics, f"{log}_claims")
    assert f.outcome.eq("PAID").mean() == pytest.approx(mv(metrics, f"{log}_paid_rate"), abs=1e-12)
    assert f.rejected_any.mean() == pytest.approx(mv(metrics, f"{log}_org_rejection_rate"), abs=1e-12)
    assert f.resubmitted.mean() == pytest.approx(mv(metrics, f"{log}_resubmission_rate"), abs=1e-12)
    assert f.first_time_right.mean() == pytest.approx(mv(metrics, f"{log}_first_time_right_rate"), abs=1e-12)
    assert (paid.cycle_days > SLA_DAYS).mean() == pytest.approx(mv(metrics, f"{log}_sla_breach_rate"), abs=1e-12)
    assert paid.cycle_days.median() == pytest.approx(mv(metrics, f"{log}_median_cycle_days"), abs=1e-4)


def test_rag_bands(fact, metrics):
    s = fact[fact.rag != "NOT SCORED"]
    assert len(s) == mv(metrics, "model_n_test")
    for band in ["RED", "AMBER", "GREEN"]:
        b = s[s.rag == band]
        assert len(b) == mv(metrics, f"rag_{band}_claims")
        assert b.problem_claim.mean() == pytest.approx(mv(metrics, f"rag_{band}_problem_rate"), abs=1e-12)
    cap = s[s.rag.isin(["RED", "AMBER"])].problem_claim.sum() / s.problem_claim.sum()
    assert cap == pytest.approx(mv(metrics, "rag_red_amber_capture"), abs=1e-12)


def test_model_auc_recomputes(fact, metrics):
    from sklearn.metrics import roc_auc_score
    s = fact[fact.rag != "NOT SCORED"]
    # isotonic calibration is monotone, so it can only merge ranks (ties); AUC on the calibrated
    # score is therefore at most the audited raw-score AUC and close to it
    auc_cal = roc_auc_score(s.problem_claim, s.risk_score)
    assert auc_cal <= mv(metrics, "model_test_auc") + 1e-9
    assert auc_cal > mv(metrics, "model_test_auc") - 0.02


def test_conformance_rate(fact, metrics):
    assert fact.conforms_to_reference.mean() == pytest.approx(mv(metrics, "ALL_conform_rate"), abs=1e-12)


def test_kpi_csv_matches_audit(metrics):
    t = pd.read_csv(ROOT / "outputs" / "kpis" / "kpi_by_log.csv").set_index("log")
    for log in ["DD", "ID", "RFP", "ALL"]:
        assert t.loc[log, "claims"] == mv(metrics, f"{log}_claims")
        assert t.loc[log, "sla_breach_rate"] == pytest.approx(mv(metrics, f"{log}_sla_breach_rate"), abs=1e-12)


def test_payment_batch_share(metrics):
    b = pd.read_csv(ROOT / "outputs" / "mining" / "payment_batch_profile.csv")
    top2 = b.groupby("weekday").payments.sum().nlargest(2)
    assert top2.sum() / b.payments.sum() == pytest.approx(mv(metrics, "payments_top2_days_share"), abs=1e-12)
    assert b.payments.sum() == mv(metrics, "payments_total")


def test_excel_cached_values_match_audit(metrics):
    from openpyxl import load_workbook
    ws = load_workbook(ROOT / "outputs" / "excel" / "ClaimPulse_KPI_Pack.xlsx", data_only=True)["Dashboard"]
    if ws["B9"].value is None:
        pytest.skip("workbook not recalculated yet (Excel recalculates on open)")
    assert ws["C3"].value == "ALL" and ws["C4"].value == SLA_DAYS
    assert ws["B9"].value == mv(metrics, "ALL_claims")
    assert ws["B10"].value == pytest.approx(mv(metrics, "ALL_paid_rate"), abs=1e-9)
    assert ws["B15"].value == pytest.approx(mv(metrics, "ALL_sla_breach_rate"), abs=1e-9)
