"""Build the GitHub-ready zip. Raw data, the warehouse and event-level exports are excluded."""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDE_DIRS = {"warehouse", "__pycache__", ".pytest_cache", ".ipynb_checkpoints"}
EXCLUDE_FILES = {"outputs/powerbi/fact_transition.csv"}
EXCLUDE_SUFFIXES = {".xes", ".gz", ".duckdb", ".wal", ".pyc"}


def release_files() -> list[Path]:
    out = []
    for p in sorted(ROOT.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(ROOT)
        if set(rel.parts) & EXCLUDE_DIRS or rel.as_posix() in EXCLUDE_FILES or p.suffix in EXCLUDE_SUFFIXES:
            continue
        if rel.parts[:2] == ("data", "raw") and p.name != ".gitkeep":
            continue
        out.append(rel)
    return out


def main(dest: str):
    dest = Path(dest)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in release_files():
            z.write(ROOT / rel, f"claimpulse/{rel.as_posix()}")
    print(f"wrote {dest} ({dest.stat().st_size:,} bytes, {len(release_files())} files)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else str(ROOT.parent / "claimpulse_github.zip"))
