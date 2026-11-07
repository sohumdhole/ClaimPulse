"""Step 1: data layer orchestration and audit."""
from __future__ import annotations

import duckdb

from . import gold, ingest, quality
from .audit import Audit
from .config import CENSOR_DAYS, OUT, WAREHOUSE


def run():
    a = Audit("step1_data_layer")
    manifest = ingest.run()
    for f in manifest["files"]:
        a.put(f"src_{f['log']}_cases", f["parsed_cases"], "int", "bronze parse of XES")
        a.put(f"src_{f['log']}_events", f["parsed_events"], "int", "bronze parse of XES")
        a.put(f"src_{f['log']}_md5_match", "yes" if f["md5_match"] else "NO", source="manifest.json")
        a.put(f"src_{f['log']}_counts_match", "yes" if f["counts_match"] else "NO", source="manifest.json")
    q = quality.run()
    for k in ["bronze_cases", "bronze_events", "silver_cases", "silver_events", "quarantined_cases", "quarantined_events"]:
        a.put(f"dq_{k}", q[k], "int", "dq/silver tables")
    for rid, n in q["rule_hits"].items():
        a.put(f"dq_{rid.lower()}_hits", n, "int", "dq.rule_results")
    gold.run()

    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    a.put("gold_claims", con.execute("SELECT count(*) FROM gold.claim").fetchone()[0], "int", "gold.claim")
    for log, n in con.execute("SELECT log, count(*) FROM gold.claim GROUP BY 1").fetchall():
        a.put(f"gold_{log}_claims", n, "int", "gold.claim")
    a.put("gold_transitions", con.execute("SELECT count(*) FROM gold.transition").fetchone()[0], "int", "gold.transition")
    a.put("censor_days", CENSOR_DAYS, "int", "config.CENSOR_DAYS")
    a.put("gold_censored_claims", con.execute("SELECT count(*) FROM gold.claim WHERE censored").fetchone()[0], "int", "gold.claim")
    lo, hi = con.execute("SELECT min(submit_month), max(submit_month) FROM gold.claim").fetchone()
    a.put("first_submit_month", lo.strftime("%B %Y"), source="gold.claim")
    a.put("last_submit_month", hi.strftime("%B %Y"), source="gold.claim")
    for log, h in con.execute("SELECT log, horizon_ts FROM silver.log_horizon").fetchall():
        a.put(f"horizon_{log}", h.strftime("%d %B %Y"), source="silver.log_horizon")
    rules = con.execute("""SELECT r.rule_id, r.level, r.action, r.description, x.hits
                           FROM dq.rules r JOIN dq.rule_results x USING (rule_id) ORDER BY 1""").df()
    rules.to_csv(OUT / "kpis" / "dq_rule_results.csv", index=False)
    con.close()
    a.save(["outputs/audit/manifest.json", "outputs/kpis/dq_rule_results.csv"])
