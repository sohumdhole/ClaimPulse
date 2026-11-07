"""Every number in the documentation must trace to the audit record."""
import copy
import re

import pytest
from conftest import ROOT

from claimpulse import render_docs
from claimpulse.config import TEMPLATES

MD_TEMPLATES = [t for t in render_docs.TARGETS if t.endswith(".md.tmpl")]


def test_all_docs_match_fresh_render(metrics):
    assert render_docs.verify(metrics) == [], "a document is stale: rerun the pipeline (step 6)"


def test_every_placeholder_exists_in_audit(metrics):
    for t in render_docs.TARGETS:
        keys = render_docs.PH.findall((TEMPLATES / t).read_text())
        missing = sorted(set(keys) - set(metrics))
        assert not missing, f"{t}: {missing}"


def test_every_quoted_metric_has_provenance(metrics):
    for t in render_docs.TARGETS:
        for k in set(render_docs.PH.findall((TEMPLATES / t).read_text())):
            assert metrics[k]["step"] and metrics[k]["source"], k


def _static_numbers(text: str) -> list[str]:
    """Numbers in template text that do NOT come from a placeholder."""
    text = render_docs.PH.sub(" ", text)
    text = re.sub(r"`[^`]*`", " ", text)                      # code and file names
    text = re.sub(r"\]\([^)]*\)", "]", text)                  # link targets
    text = re.sub(r"\[doi:[^\]]*\]", " ", text)               # DOI link text
    text = re.sub(r"(?m)^\s*#+\s*\d+\.", " ", text)           # numbered headings
    text = re.sub(r"(?m)^#+ Page \d+", " ", text)             # page headings
    text = re.sub(r"(?m)^\s*\d+\.\s", " ", text)              # numbered lists
    text = re.sub(r"\*\*\d+\.", " ", text)                    # bold numbered findings
    text = re.sub(r"(?m)^\|\s*\d\s", "| ", text)              # step column in tables
    text = re.sub(r"\b(2017|2018|2019|2020)\b", " ", text)    # years of the dataset
    text = re.sub(r"CC BY-NC 4\.0|4TU|MD5|SHA-256|P90", " ", text)
    return re.findall(r"(?<![A-Za-z_\d.,])\d[\d,.]*", text)


@pytest.mark.parametrize("tmpl", MD_TEMPLATES)
def test_templates_have_no_hardcoded_numbers(tmpl):
    found = _static_numbers((TEMPLATES / tmpl).read_text())
    assert found == [], f"{tmpl} has numbers not sourced from the audit: {found}"


def test_static_number_scanner_catches_a_planted_number():
    assert _static_numbers("The median is {{ALL_median_cycle_days}} days, P95 is 42 days.") == ["42"]


def test_mutation_audit_value_change_is_detected(metrics):
    """Mutation check: if a number in the audit changes, the committed README no longer matches."""
    m = copy.deepcopy(metrics)
    m["ALL_claims"]["display"] = "99,999"
    assert "README.md" in render_docs.verify(m)


def test_mutation_readme_edit_is_detected(metrics, tmp_path, monkeypatch):
    """Mutation check: hand-editing a number in the README is detected."""
    readme = (ROOT / "README.md").read_text()
    shown = metrics["ALL_sla_breach_rate"]["display"]
    assert shown in readme
    fake_root = tmp_path
    (fake_root / "docs").mkdir()
    (fake_root / "outputs" / "powerbi").mkdir(parents=True)
    for dst in render_docs.TARGETS.values():
        (fake_root / dst).write_text((ROOT / dst).read_text())
    (fake_root / "README.md").write_text(readme.replace(shown, "11.1%", 1))
    monkeypatch.setattr(render_docs, "ROOT", fake_root)
    assert render_docs.verify(metrics) == ["README.md"]


def test_readme_headline_numbers_present(metrics):
    readme = (ROOT / "README.md").read_text()
    for k in ["ALL_claims", "model_test_auc", "ALL_sla_breach_rate", "wait_share_post_approval", "ALL_conform_rate"]:
        assert metrics[k]["display"] in readme, k
