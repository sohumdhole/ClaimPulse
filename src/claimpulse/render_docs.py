"""Step 6: render README and docs from templates and the audit record.

Templates use {{metric_key}} placeholders. Rendering fails on an unknown key, so
a document can only quote numbers that exist in outputs/audit/audit.json. The
static template text is checked separately by tests for stray numbers.
"""
from __future__ import annotations

import json
import re

from .audit import load_metrics
from .config import AUDIT_PATH, LICENCE, ROOT, SOURCES, TEMPLATES

PH = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")
FORBIDDEN = ["\u2014", "\u2013"]  # em dash, en dash

TARGETS = {
    "README.md.tmpl": "README.md",
    "methodology.md.tmpl": "docs/methodology.md",
    "reporting_requirements.md.tmpl": "docs/reporting_requirements.md",
    "powerbi_build_guide.md.tmpl": "docs/powerbi_build_guide.md",
    "measures.dax.tmpl": "outputs/powerbi/measures.dax",
}


def render_text(template: str, metrics: dict) -> tuple[str, list[str]]:
    used = []

    def sub(m):
        k = m.group(1)
        if k not in metrics:
            raise KeyError(f"Template references unknown audit key: {k}")
        used.append(k)
        return str(metrics[k]["display"])

    return PH.sub(sub, template), used


def data_readme() -> str:
    """Download instructions generated from the source registry in config.py."""
    lines = ["# Raw data (not committed)", "",
             f"The pipeline needs three files from 4TU.ResearchData (licence {LICENCE}). Put them in this folder unchanged (do not unzip the .gz).", ""]
    for i, (log, s) in enumerate(SOURCES.items(), 1):
        lines += [f"{i}. **{s['name']}**: open {s['url']}, scroll to DATA and click `{s['file']}`.",
                  f"   Expected size {s['published_bytes']:,} bytes, MD5 `{s['published_md5']}`, DOI {s['doi']}.", ""]
    lines += ["If you downloaded the 'download all files (zip)' bundle instead, unzip it and copy only the `.xes.gz` file.",
              "", "The ingest step checks every MD5 and stops if a file differs from the published version."]
    return "\n".join(lines) + "\n"


def verify(metrics: dict | None = None) -> list[str]:
    """Return the documents whose committed text differs from a fresh render."""
    metrics = metrics or load_metrics()
    stale = []
    for src, dst in TARGETS.items():
        expected, _ = render_text((TEMPLATES / src).read_text(), metrics)
        if (ROOT / dst).read_text() != expected:
            stale.append(dst)
    return stale


def run(metrics: dict | None = None) -> dict:
    metrics = metrics or load_metrics()
    trace = {}
    for src, dst in TARGETS.items():
        text, used = render_text((TEMPLATES / src).read_text(), metrics)
        for ch in FORBIDDEN:
            if ch in text:
                raise ValueError(f"{dst} contains a forbidden dash character")
        out = ROOT / dst
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        trace[dst] = {k: metrics[k]["display"] for k in sorted(set(used))}
    (AUDIT_PATH.parent / "doc_trace.json").write_text(json.dumps(trace, indent=2))
    (ROOT / "data" / "README.md").write_text(data_readme())
    return trace
