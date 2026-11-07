"""Audit record: the single source of truth for every number quoted in the docs.

Each metric is stored with its raw value, a display string and the step and
artefact that produced it. Documentation is rendered only from this record.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone

from .config import AUDIT_PATH


def _load() -> dict:
    if AUDIT_PATH.exists():
        return json.loads(AUDIT_PATH.read_text())
    return {"metrics": {}, "steps": {}}


def fmt(value, kind: str = "auto") -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    if kind == "pct":
        return f"{value * 100:.1f}%"
    if kind == "int":
        return f"{int(round(value)):,}"
    if kind == "d1":
        return f"{value:.1f}"
    if kind == "d2":
        return f"{value:.2f}"
    if kind == "d3":
        return f"{value:.3f}"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


class Audit:
    def __init__(self, step: str):
        self.step = step
        self.data = _load()
        self.data["metrics"] = {k: v for k, v in self.data["metrics"].items() if v.get("step") != step}

    def put(self, key: str, value, kind: str = "auto", source: str = ""):
        if isinstance(value, (int,)) and not isinstance(value, bool):
            value = int(value)
        elif hasattr(value, "item"):
            value = value.item()
        self.data["metrics"][key] = {
            "value": value,
            "display": fmt(value, kind),
            "kind": kind,
            "step": self.step,
            "source": source,
        }

    def save(self, artefacts: list[str] | None = None):
        self.data["steps"][self.step] = {
            "completed_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "artefacts": sorted(artefacts or []),
        }
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.data["metrics"] = dict(sorted(self.data["metrics"].items()))
        AUDIT_PATH.write_text(json.dumps(self.data, indent=2, ensure_ascii=False))


def load_metrics() -> dict:
    return _load()["metrics"]
