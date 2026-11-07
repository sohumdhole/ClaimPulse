"""Central configuration: paths, source registry and analytical definitions.

Every analytical threshold used anywhere in the project is defined here so that
it is visible, versioned and quoted in the documentation from one place.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw"
WAREHOUSE = ROOT / "warehouse" / "claimpulse.duckdb"
OUT = ROOT / "outputs"
AUDIT_PATH = OUT / "audit" / "audit.json"
FIG_DIR = OUT / "figures"
TEMPLATES = ROOT / "templates"
DOCS = ROOT / "docs"

# Source registry. Published values were read from the 4TU.ResearchData landing
# pages; the ingest step checks every file against them and records the result.
SOURCES = {
    "DD": {
        "name": "Domestic Declarations",
        "file": "DomesticDeclarations.xes.gz",
        "doi": "10.4121/uuid:3f422315-ed9d-4882-891f-e180b5b4feb5",
        "url": "https://data.4tu.nl/datasets/6a0a26d2-82d0-4018-b1cd-89afb0e8627f",
        "published_md5": "6a78c39491498363ce4788e0e8ca75ef",
        "published_bytes": 933930,
        "published_cases": 10500,
        "published_events": 56437,
        "doc_prefix": "Declaration",
    },
    "ID": {
        "name": "International Declarations",
        "file": "InternationalDeclarations.xes.gz",
        "doi": "10.4121/uuid:2bbf8f6a-fc50-48eb-aa9e-c4ea5ef7e8c5",
        "url": "https://data.4tu.nl/articles/_/12687374/1",
        "published_md5": "1ec65e046f70bb399cc6d2c154cd615a",
        "published_bytes": 1581144,
        "published_cases": 6449,
        "published_events": 72151,
        "doc_prefix": "Declaration",
    },
    "RFP": {
        "name": "Request For Payment",
        "file": "RequestForPayment.xes.gz",
        "doi": "10.4121/uuid:895b26fb-6f25-46eb-9e48-0dca26fcd030",
        "url": "https://data.4tu.nl/articles/_/12706886/1",
        "published_md5": "2eb4dd20e70b8de4e32cc3c239bde7f2",
        "published_bytes": 696896,
        "published_cases": 6886,
        "published_events": 36796,
        "doc_prefix": "Request For Payment",
    },
}
LICENCE = "CC BY-NC 4.0"
CITATION = "van Dongen, B. (2020). BPI Challenge 2020. 4TU.ResearchData. https://doi.org/10.4121/uuid:52fb97d4-4588-43c9-9d04-3604d4613b51"

# Roles that can reject on behalf of the organisation. "REJECTED by EMPLOYEE"
# is the employee closing or withdrawing after a rejection, so it is not counted
# as an organisational rejection.
REVIEW_ROLES = ["ADMINISTRATION", "PRE_APPROVER", "BUDGET OWNER", "SUPERVISOR", "DIRECTOR", "MISSING"]

# Analytical service level (NOT a TU/e policy, which the data does not contain):
# a paid claim breaches the SLA when first submission to "Payment Handled"
# exceeds this many calendar days.
SLA_DAYS = 14
SLA_SENSITIVITY = [7, 14, 21, 30]
# Default RAG thresholds for the monthly SLA breach rate (editable in Excel and Power BI).
SLA_STATUS_AMBER = 0.20
SLA_STATUS_RED = 0.30

# Right-censoring guard: claims first submitted within this many days of the
# last claim event in their log may still be open at extraction, so they are
# excluded from outcome-based KPIs and from model training/testing.
CENSOR_DAYS = 90

# International declarations should be filed within two months after the trip
# (dataset description); checked as a compliance rule.
DECLARE_WINDOW_DAYS = 60

# Early-warning model.
MODEL_VALID_START = "2018-04-01"  # train before this; validation (tuning, calibration, RAG cut-offs) until test start
MODEL_TEST_START = "2018-07-01"   # untouched temporal hold-out: everything submitted on/after this date
RAG_RED_TOP = 0.10                # RED = riskiest 10% of claims, cut-off fixed on validation
RAG_AMBER_TOP = 0.30              # AMBER = next 20% (top 30% minus RED), cut-off fixed on validation
HISTORY_WINDOW_DAYS = 180         # org unit / budget history looks back this many days (rolling)
HISTORY_SMOOTHING = 20            # pseudo-count shrinking small-history rates toward the training base rate
RANDOM_STATE = 42
CI_LEVEL = 0.95                   # bootstrap confidence level for test AUC
BOOTSTRAP_RESAMPLES = 1000
