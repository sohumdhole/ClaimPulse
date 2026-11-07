"""Run the full ClaimPulse pipeline: python -m claimpulse.pipeline [--from-step N]"""
from __future__ import annotations

import argparse
import time
import warnings

warnings.filterwarnings("ignore")

STEPS = [
    ("1 data layer", "data_layer"),
    ("2 operational KPIs", "kpis"),
    ("3 process mining", "mining"),
    ("4 early-warning model", "model"),
    ("5 Power BI export", "powerbi_export"),
    ("5 Excel pack", "excel_pack"),
    ("6 render docs", "render_docs"),
]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-step", type=int, default=1)
    args = ap.parse_args(argv)
    import importlib
    for label, mod in STEPS:
        if int(label.split()[0]) < args.from_step:
            continue
        t = time.time()
        importlib.import_module(f"claimpulse.{mod}").run()
        print(f"[ok] step {label} ({time.time() - t:.1f}s)")


if __name__ == "__main__":
    main()
