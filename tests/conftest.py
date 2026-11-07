import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from claimpulse.config import AUDIT_PATH, WAREHOUSE  # noqa: E402


@pytest.fixture(scope="session")
def metrics():
    return json.loads(AUDIT_PATH.read_text())["metrics"]


@pytest.fixture(scope="session")
def con():
    """Read-only warehouse connection. Skipped when the raw data has not been processed (e.g. in CI)."""
    if not WAREHOUSE.exists():
        pytest.skip("warehouse not built (raw data is not shipped); run the pipeline first")
    import duckdb
    c = duckdb.connect(str(WAREHOUSE), read_only=True)
    c.execute("SET TimeZone='UTC'")
    yield c
    c.close()


def mv(metrics, key):
    return metrics[key]["value"]
