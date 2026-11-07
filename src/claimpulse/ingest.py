"""Step 1a: provenance manifest and bronze layer.

Bronze is a lossless, long-format copy of every XES trace attribute and every
event attribute, so nothing in the source is dropped before quality rules run.
"""
from __future__ import annotations

import gzip
import hashlib
import json
from datetime import datetime, timezone

import duckdb
import pandas as pd
from lxml import etree

from .config import CITATION, LICENCE, OUT, RAW_DIR, SOURCES, WAREHOUSE


def file_hashes(path) -> dict:
    md5, sha = hashlib.md5(), hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            md5.update(chunk)
            sha.update(chunk)
    return {"md5": md5.hexdigest(), "sha256": sha.hexdigest(), "bytes": path.stat().st_size}


def parse_xes(path, log: str):
    """Stream-parse one XES file into (trace_attr_rows, event_rows)."""
    trace_rows, event_rows = [], []
    with gzip.open(path) as fh:
        for _, trace in etree.iterparse(fh, events=("end",), tag="{*}trace"):
            attrs = {}
            for child in trace:
                tag = etree.QName(child).localname
                if tag != "event":
                    attrs[child.get("key")] = (tag, child.get("value"))
            case_id = attrs.get("concept:name", (None, None))[1]
            for key, (xtype, val) in attrs.items():
                trace_rows.append((log, case_id, key, xtype, val))
            seq = 0
            for child in trace:
                if etree.QName(child).localname != "event":
                    continue
                d = {g.get("key"): g.get("value") for g in child}
                extra = {k: v for k, v in d.items() if k not in
                         ("id", "concept:name", "time:timestamp", "org:resource", "org:role")}
                event_rows.append((log, case_id, seq, d.get("id"), d.get("concept:name"),
                                   d.get("time:timestamp"), d.get("org:resource"), d.get("org:role"),
                                   json.dumps(extra) if extra else None))
                seq += 1
            trace.clear()
    return trace_rows, event_rows


def run() -> dict:
    WAREHOUSE.parent.mkdir(parents=True, exist_ok=True)
    if WAREHOUSE.exists():
        WAREHOUSE.unlink()
    con = duckdb.connect(str(WAREHOUSE))
    con.execute("CREATE SCHEMA bronze; CREATE SCHEMA silver; CREATE SCHEMA gold; CREATE SCHEMA dq;")

    manifest = {"generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "licence": LICENCE, "citation": CITATION, "files": []}
    all_tr, all_ev = [], []
    for log, src in SOURCES.items():
        path = RAW_DIR / src["file"]
        if not path.exists():
            raise FileNotFoundError(f"Missing raw file {path}. See data/README.md for download steps.")
        h = file_hashes(path)
        tr, ev = parse_xes(path, log)
        n_cases = len({r[1] for r in tr})
        entry = {
            "log": log, "name": src["name"], "file": src["file"], "doi": src["doi"], "url": src["url"],
            **h, "published_md5": src["published_md5"], "md5_match": h["md5"] == src["published_md5"],
            "parsed_cases": n_cases, "parsed_events": len(ev),
            "published_cases": src["published_cases"], "published_events": src["published_events"],
            "counts_match": n_cases == src["published_cases"] and len(ev) == src["published_events"],
        }
        if not entry["md5_match"]:
            raise ValueError(f"{src['file']} MD5 {h['md5']} does not match published {src['published_md5']}")
        manifest["files"].append(entry)
        all_tr += tr
        all_ev += ev

    tr_df = pd.DataFrame(all_tr, columns=["log", "case_id", "attr_key", "xes_type", "attr_value"])
    ev_df = pd.DataFrame(all_ev, columns=["log", "case_id", "event_seq", "event_id", "activity",
                                          "ts_raw", "resource", "role", "extra_json"])
    con.register("tr_df", tr_df)
    con.register("ev_df", ev_df)
    con.execute("CREATE TABLE bronze.trace_attr AS SELECT * FROM tr_df")
    con.execute("CREATE TABLE bronze.event AS SELECT * FROM ev_df")
    con.close()

    (OUT / "audit").mkdir(parents=True, exist_ok=True)
    (OUT / "audit" / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest
