"""Release hygiene: no raw data, no forbidden characters or names, Excel pack is formula-driven."""
from conftest import ROOT
from make_release import release_files

TEXT_SUFFIXES = {".md", ".py", ".tmpl", ".dax", ".txt", ".json", ".yml", ".ini", ".csv", ".cfg", ".toml"}


def _texts():
    for rel in release_files():
        if (ROOT / rel).suffix in TEXT_SUFFIXES or rel.name in {"LICENSE", ".gitignore"}:
            yield rel, (ROOT / rel).read_text(errors="ignore")


def test_no_raw_data_in_release():
    files = release_files()
    assert not [f for f in files if f.suffix in {".xes", ".gz", ".duckdb"}]
    assert not [f for f in files if f.parts[:2] == ("data", "raw") and f.name != ".gitkeep"]
    assert "outputs/powerbi/fact_transition.csv" not in {f.as_posix() for f in files}


def test_no_em_or_en_dashes():
    bad = [str(rel) for rel, t in _texts() if chr(0x2014) in t or chr(0x2013) in t]
    assert bad == []


def test_no_private_names():
    banned = ["career" + "twin", "fin" + "core"]  # spelled in parts so this file does not match itself
    bad = [str(rel) for rel, t in _texts() for b in banned if b in t.lower()]
    assert bad == []


def test_gitignore_blocks_raw_data():
    gi = (ROOT / ".gitignore").read_text()
    assert "data/raw/*" in gi and "warehouse/" in gi


def test_excel_pack_is_formula_driven():
    from openpyxl import load_workbook
    wb = load_workbook(ROOT / "outputs" / "excel" / "ClaimPulse_KPI_Pack.xlsx")
    db = wb["Dashboard"]
    assert str(db["B9"].value).startswith("=COUNTIFS(")
    assert str(db["H19"].value).startswith("=COUNTIFS(")
    assert any("C3" in str(dv.sqref) for dv in db.data_validations.dataValidation)
    assert len(db.conditional_formatting) > 0
    assert len(db._charts) == 2 and len(wb["Org_Scorecard"]._charts) == 1 and len(wb["RAG_Monitor"]._charts) == 2
    n_formulas = sum(1 for ws in wb.worksheets for row in ws.iter_rows() for c in row
                     if isinstance(c.value, str) and c.value.startswith("="))
    assert n_formulas > 300
