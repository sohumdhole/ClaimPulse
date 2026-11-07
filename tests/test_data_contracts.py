"""Contracts on the warehouse and provenance. Warehouse tests skip when raw data is absent."""
import gzip
import json

import pandas as pd
import pytest
from conftest import ROOT, mv

from claimpulse import ingest
from claimpulse.config import SLA_DAYS, SOURCES


def test_manifest_hashes_and_counts():
    m = json.loads((ROOT / "outputs" / "audit" / "manifest.json").read_text())
    assert {f["log"] for f in m["files"]} == set(SOURCES)
    for f in m["files"]:
        assert f["md5_match"] and f["counts_match"]
        assert f["md5"] == SOURCES[f["log"]]["published_md5"]
        assert f["parsed_cases"] == SOURCES[f["log"]]["published_cases"]
        assert f["parsed_events"] == SOURCES[f["log"]]["published_events"]
    assert m["licence"] == "CC BY-NC 4.0"


def test_xes_parser_on_synthetic_fixture(tmp_path):
    """TEST-ONLY synthetic fixture: two hand-written traces, never used for any result."""
    xes = """<?xml version="1.0" encoding="UTF-8"?><log xes.version="1.0">
    <trace><string key="concept:name" value="TEST case A"/><float key="Amount" value="10.5"/>
      <event><string key="id" value="e1"/><string key="concept:name" value="Declaration SUBMITTED by EMPLOYEE"/>
        <date key="time:timestamp" value="2018-01-01T09:00:00.000+01:00"/><string key="org:resource" value="STAFF MEMBER"/>
        <string key="org:role" value="EMPLOYEE"/></event>
      <event><string key="id" value="e2"/><string key="concept:name" value="Payment Handled"/>
        <date key="time:timestamp" value="2018-01-04T17:30:00.000+01:00"/><string key="org:resource" value="SYSTEM"/>
        <string key="org:role" value="UNDEFINED"/></event></trace>
    <trace><string key="concept:name" value="TEST case B"/><float key="Amount" value="0"/></trace></log>"""
    p = tmp_path / "synthetic_test_only.xes.gz"
    with gzip.open(p, "wt") as fh:
        fh.write(xes)
    tr, ev = ingest.parse_xes(p, "TEST")
    assert len({r[1] for r in tr}) == 2
    assert [e[4] for e in ev] == ["Declaration SUBMITTED by EMPLOYEE", "Payment Handled"]
    assert ev[0][1] == "TEST case A" and ev[1][2] == 1


def test_case_conservation(con, metrics):
    bronze = con.execute("SELECT count(DISTINCT log||case_id) FROM bronze.trace_attr").fetchone()[0]
    silver = con.execute("SELECT count(*) FROM silver.case").fetchone()[0]
    quar = con.execute("SELECT count(DISTINCT log||case_id) FROM dq.quarantine_case").fetchone()[0]
    assert bronze == silver + quar
    assert silver == mv(metrics, "gold_claims")


def test_gold_claim_contract(con):
    df = con.execute("SELECT * FROM gold.claim").df()
    assert not df.duplicated(["log", "case_id"]).any()
    assert set(df.outcome) <= {"PAID", "REJECTED_CLOSED", "OPEN"}
    paid = df[df.outcome == "PAID"]
    assert paid.cycle_days.notna().all() and (paid.cycle_days >= 0).all()
    assert df.first_submit_ts.notna().all()
    assert (df.n_submissions >= 1).all()
    # derived flags are consistent with their definitions
    assert (df.first_time_right == ((df.outcome == "PAID") & ~df.rejected_any & ~df.resubmitted)).all()
    exp = df.rejected_any | (df.outcome != "PAID") | (df.cycle_days.fillna(0) > SLA_DAYS)
    assert (df.problem_claim == exp).all()


def test_transitions_non_negative(con):
    assert con.execute("SELECT count(*) FROM gold.transition WHERE wait_hours < 0").fetchone()[0] == 0


def test_model_features_are_point_in_time(con):
    """Permit features only use permit events dated before the claim's first submission,
    and history features only use claims closed before it."""
    from claimpulse.config import HISTORY_WINDOW_DAYS
    from claimpulse.model import build_features
    df = build_features(con)
    ids = df[(df.log == "ID") & (df.id_permit_approved == 1)].head(300)
    for r in ids.itertuples():
        t = con.execute("""SELECT min(ts_utc) FROM silver.event WHERE log='ID' AND case_id=? AND doc_type='PERMIT'
                           AND action='FINAL_APPROVED'""", [r.case_id]).fetchone()[0]
        assert pd.Timestamp(t) < r.first_submit_ts
    # brute-force org history for a sample of RFP claims
    claims = con.execute("""SELECT g.log, g.case_id, g.org_unit, g.first_submit_ts, g.problem_claim, l.close_ts
        FROM gold.claim g JOIN (SELECT log, case_id, max(ts_utc) close_ts FROM silver.event
        WHERE doc_type IN ('CLAIM','PAYMENT') GROUP BY 1,2) l USING (log, case_id) WHERE NOT g.censored""").df()
    claims["first_submit_ts"] = pd.to_datetime(claims.first_submit_ts, utc=True)
    claims["close_ts"] = pd.to_datetime(claims.close_ts, utc=True)
    sample = df[(df.log == "RFP")].sample(50, random_state=0)
    for r in sample.itertuples():
        me = claims[(claims.log == r.log) & (claims.case_id == r.case_id)].iloc[0]
        prior = claims[(claims.org_unit == me.org_unit) & (claims.close_ts < me.first_submit_ts)
                       & (claims.close_ts >= me.first_submit_ts - pd.Timedelta(days=HISTORY_WINDOW_DAYS))]
        if me.org_unit in (None, "UNKNOWN"):
            continue
        assert (0 if pd.isna(r.org_n) else r.org_n) == len(prior)
