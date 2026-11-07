"""Step 5a: formula-driven Excel KPI pack.

Every KPI cell is a COUNTIFS / AVERAGEIFS formula over the claim-level sheet, so
changing the log dropdown or the SLA threshold recalculates the whole pack.
"""
from __future__ import annotations

import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.comments import Comment
from openpyxl.formatting.rule import CellIsRule, ColorScaleRule, FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from .audit import Audit
from .config import CITATION, LICENCE, OUT, SLA_DAYS, SLA_STATUS_AMBER, SLA_STATUS_RED
from .kpis import ORG_MIN

F = "Arial"
H1 = Font(name=F, size=14, bold=True)
B = Font(name=F, bold=True)
N = Font(name=F)
INPUT = Font(name=F, color="0000FF", bold=True)
WHITE = Font(name=F, color="FFFFFF", bold=True)
HEAD_FILL = PatternFill("solid", fgColor="1C5CAB")
IN_FILL = PatternFill("solid", fgColor="FFFF00")
TILE_FILL = PatternFill("solid", fgColor="EEF4FC")
RED_F, AMB_F, GRN_F = (PatternFill("solid", fgColor=c) for c in ("F4C7C7", "FDE9B8", "CDEFCD"))
THIN = Border(bottom=Side(style="thin", color="D0D0D0"))


def _hdr(ws, row, headers, col=1):
    for i, h in enumerate(headers):
        c = ws.cell(row=row, column=col + i, value=h)
        c.font, c.fill, c.alignment = WHITE, HEAD_FILL, Alignment(horizontal="center", wrap_text=True, vertical="center")


def run():
    a = Audit("step5_excel")
    fact = pd.read_csv(OUT / "powerbi" / "fact_claim.csv")
    fact["submit_month"] = pd.to_datetime(fact.submit_date_key.astype(str), format="%Y%m%d").dt.to_period("M").dt.to_timestamp()
    cols = ["log_code", "case_id", "submit_month", "org_code", "outcome", "cycle_days", "rejected_any", "resubmitted",
            "first_time_right", "problem_claim", "rag"]
    data = fact[cols].sort_values(["submit_month", "log_code", "case_id"]).reset_index(drop=True)
    n = len(data)
    last = n + 1

    wb = Workbook()
    # ---------------- About ----------------
    ab = wb.active
    ab.title = "About"
    lines = [
        ("ClaimPulse KPI pack", H1),
        ("Operational KPIs for reimbursement claims: BPI Challenge 2020 (TU/e travel and expense claims).", N),
        ("", N),
        ("How to use", B),
        ("1. On the Dashboard sheet, pick a log in the yellow dropdown cell (ALL, DD, ID, RFP).", N),
        ("2. Change the SLA threshold (yellow cell, days). Every KPI, the scorecard and the charts recalculate.", N),
        ("3. Change the RAG thresholds for SLA breach rate (yellow cells) to move the RED / AMBER / GREEN status.", N),
        ("", N),
        ("Legend", B),
        ("Blue bold text on yellow fill = input you can edit. Black text = formula. Do not type over formulas.", N),
        ("", N),
        ("Sheets", B),
        ("Dashboard: KPI tiles, monthly KPI table with RAG status, two charts.", N),
        (f"Org_Scorecard: organisational units (International and Requests for payment) with {ORG_MIN} or more claims.", N),
        ("RAG_Monitor: early-warning model bands on the unseen test period (July to December 2018).", N),
        ("Definitions: KPI dictionary. Claims: one row per claim (data for every formula).", N),
        ("", N),
        ("Logs: DD = Domestic declarations, ID = International declarations, RFP = Requests for payment.", N),
        (f"Source: {CITATION}. Licence: {LICENCE}. Claim rows are derived from the event logs.", N),
        ("Amounts are as published and not interpreted as euros. Resources in the source are anonymised.", N),
    ]
    for i, (t, f) in enumerate(lines, 1):
        ab.cell(row=i, column=1, value=t).font = f
    ab.column_dimensions["A"].width = 120

    # ---------------- Claims ----------------
    cl = wb.create_sheet("Claims")
    heads = ["Log", "Claim id", "Submit month", "Org unit / budget", "Outcome", "Cycle days", "Rejected (0/1)",
             "Resubmitted (0/1)", "First time right (0/1)", "Problem claim (0/1)", "RAG (test period)"]
    _hdr(cl, 1, heads)
    for r in data.itertuples(index=False):
        cl.append([r.log_code, r.case_id, r.submit_month.to_pydatetime(), r.org_code, r.outcome,
                   None if pd.isna(r.cycle_days) else float(r.cycle_days), int(r.rejected_any), int(r.resubmitted),
                   int(r.first_time_right), int(r.problem_claim), r.rag])
    for row in cl.iter_rows(min_row=2, max_row=last, min_col=3, max_col=3):
        for c in row:
            c.number_format = "mmm yyyy"
    for i, w in enumerate([6, 26, 12, 28, 16, 11, 11, 12, 13, 13, 14], 1):
        cl.column_dimensions[get_column_letter(i)].width = w
    cl.freeze_panes = "A2"
    cl.auto_filter.ref = f"A1:K{last}"

    R = lambda col: f"Claims!${col}$2:${col}${last}"  # noqa: E731
    LOG, MON, ORG, OUTC, CYC, REJ, RES, FTR, PROB, RAG = (R(c) for c in "ACDEFGHIJK")

    # ---------------- Dashboard ----------------
    db = wb.create_sheet("Dashboard", 1)
    db["A1"] = "ClaimPulse: reimbursement claims KPI dashboard"
    db["A1"].font = H1
    inputs = [("Log (dropdown)", "ALL", "Pick ALL, DD, ID or RFP"),
              ("SLA threshold (days)", SLA_DAYS, "Analytical SLA: a paid claim breaches when submission to payment exceeds this many days. Not a TU/e policy."),
              ("AMBER if SLA breach rate at or above", SLA_STATUS_AMBER, "Analytical RAG threshold, editable"),
              ("RED if SLA breach rate at or above", SLA_STATUS_RED, "Analytical RAG threshold, editable")]
    for i, (lab, val, note) in enumerate(inputs, 3):
        db.cell(row=i, column=1, value=lab).font = B
        c = db.cell(row=i, column=3, value=val)
        c.font, c.fill = INPUT, IN_FILL
        c.comment = Comment(note, "ClaimPulse")
        if isinstance(val, float):
            c.number_format = "0%"
    dv = DataValidation(type="list", formula1='"ALL,DD,ID,RFP"', allow_blank=False)
    db.add_data_validation(dv)
    dv.add("C3")
    db["E3"] = "Log filter used in formulas"
    db["E3"].font = Font(name=F, italic=True, color="808080")
    db["F3"] = '=IF($C$3="ALL","*",$C$3)'
    SEL, SLA, AMB, RED = "$F$3", "$C$4", "$C$5", "$C$6"

    tiles = [
        ("Claims", f"=COUNTIFS({LOG},{SEL})", "#,##0"),
        ("Paid", f"=COUNTIFS({LOG},{SEL},{OUTC},\"PAID\")/MAX(1,B9)", "0.0%"),
        ("Rejected at least once", f"=COUNTIFS({LOG},{SEL},{REJ},1)/MAX(1,B9)", "0.0%"),
        ("Resubmitted", f"=COUNTIFS({LOG},{SEL},{RES},1)/MAX(1,B9)", "0.0%"),
        ("First time right", f"=COUNTIFS({LOG},{SEL},{FTR},1)/MAX(1,B9)", "0.0%"),
        ("Mean cycle (days, paid)", f"=IFERROR(AVERAGEIFS({CYC},{LOG},{SEL},{OUTC},\"PAID\"),0)", "0.0"),
        ("SLA breach (paid)", f"=COUNTIFS({LOG},{SEL},{OUTC},\"PAID\",{CYC},\">\"&{SLA})/MAX(1,COUNTIFS({LOG},{SEL},{OUTC},\"PAID\"))", "0.0%"),
    ]
    db["A8"] = "Headline KPIs (selected log, all months)"
    db["A8"].font = B
    for i, (lab, fml, fmtx) in enumerate(tiles):
        r = 9 + i
        db.cell(row=r, column=1, value=lab).font = N
        c = db.cell(row=r, column=2, value=fml)
        c.number_format, c.font, c.fill = fmtx, Font(name=F, bold=True, size=12), TILE_FILL
    db["C15"] = f'=IF(B15>={RED},"RED",IF(B15>={AMB},"AMBER","GREEN"))'
    db["C15"].font = B

    months = sorted(data.submit_month.unique())
    top = 18
    db.cell(row=top - 1, column=1, value="Monthly KPIs by submission month (selected log)").font = B
    mh = ["Month", "Claims", "Paid", "Rejected %", "Resubmitted %", "First time right %", "Mean cycle (days)",
          "SLA breach %", "SLA status"]
    _hdr(db, top, mh)
    for i, m in enumerate(months):
        r = top + 1 + i
        db.cell(row=r, column=1, value=pd.Timestamp(m).to_pydatetime()).number_format = "mmm yyyy"
        mc = f"{MON},$A{r}"
        db.cell(row=r, column=2, value=f"=COUNTIFS({LOG},{SEL},{mc})")
        db.cell(row=r, column=3, value=f"=COUNTIFS({LOG},{SEL},{mc},{OUTC},\"PAID\")")
        db.cell(row=r, column=4, value=f"=COUNTIFS({LOG},{SEL},{mc},{REJ},1)/MAX(1,$B{r})").number_format = "0.0%"
        db.cell(row=r, column=5, value=f"=COUNTIFS({LOG},{SEL},{mc},{RES},1)/MAX(1,$B{r})").number_format = "0.0%"
        db.cell(row=r, column=6, value=f"=COUNTIFS({LOG},{SEL},{mc},{FTR},1)/MAX(1,$B{r})").number_format = "0.0%"
        db.cell(row=r, column=7, value=f"=IFERROR(AVERAGEIFS({CYC},{LOG},{SEL},{mc},{OUTC},\"PAID\"),0)").number_format = "0.0"
        db.cell(row=r, column=8, value=f"=COUNTIFS({LOG},{SEL},{mc},{OUTC},\"PAID\",{CYC},\">\"&{SLA})/MAX(1,$C{r})").number_format = "0.0%"
        db.cell(row=r, column=9, value=f'=IF($H{r}>={RED},"RED",IF($H{r}>={AMB},"AMBER","GREEN"))')
        for c in range(1, 10):
            db.cell(row=r, column=c).font = N
            db.cell(row=r, column=c).border = THIN
    end = top + len(months)
    rng = f"H{top + 1}:I{end}"
    db.conditional_formatting.add(rng, FormulaRule(formula=[f"$H{top + 1}>={RED}"], fill=RED_F))
    db.conditional_formatting.add(rng, FormulaRule(formula=[f"$H{top + 1}>={AMB}"], fill=AMB_F))
    db.conditional_formatting.add(rng, FormulaRule(formula=[f"$H{top + 1}<{AMB}"], fill=GRN_F))
    db.conditional_formatting.add("B15:C15", FormulaRule(formula=[f"$B$15>={RED}"], fill=RED_F))
    db.conditional_formatting.add("B15:C15", FormulaRule(formula=[f"$B$15>={AMB}"], fill=AMB_F))
    db.conditional_formatting.add("B15:C15", FormulaRule(formula=[f"$B$15<{AMB}"], fill=GRN_F))
    for col, w in zip("ABCDEFGHI", [34, 12, 12, 12, 14, 16, 16, 13, 11]):
        db.column_dimensions[col].width = w
    db.freeze_panes = "A4"

    ch = LineChart()
    ch.title, ch.y_axis.title, ch.height, ch.width = "Claims per month (selected log)", "claims", 7, 16
    ch.add_data(Reference(db, min_col=2, min_row=top, max_row=end), titles_from_data=True)
    ch.set_categories(Reference(db, min_col=1, min_row=top + 1, max_row=end))
    ch.x_axis.number_format = "mmm yy"
    db.add_chart(ch, "K3")
    ch2 = LineChart()
    ch2.title, ch2.y_axis.title, ch2.height, ch2.width = "SLA breach rate per month (selected log)", "% of paid claims", 7, 16
    ch2.add_data(Reference(db, min_col=8, min_row=top, max_row=end), titles_from_data=True)
    ch2.set_categories(Reference(db, min_col=1, min_row=top + 1, max_row=end))
    ch2.y_axis.number_format = "0%"
    db.add_chart(ch2, "K19")

    # ---------------- Org scorecard ----------------
    oc = wb.create_sheet("Org_Scorecard", 2)
    oc["A1"] = f"Organisational unit scorecard (units with {ORG_MIN}+ claims; uses the Dashboard log and SLA inputs)"
    oc["A1"].font = H1
    oc["A2"] = "Domestic declarations carry no org unit, so they appear only when the dropdown is ALL via their budget rows below the units."
    oc["A2"].font = Font(name=F, italic=True, color="808080")
    units = (data[~data.org_code.str.startswith("DD ")].groupby("org_code").size().loc[lambda s: s >= ORG_MIN]
             .sort_values(ascending=False).index.tolist())
    oh = ["Org unit", "Claims", "Rejected %", "SLA breach %", "First time right %", "Mean cycle (days)",
          "Rejected % vs all", "SLA breach % vs all"]
    _hdr(oc, 4, oh)
    for i, u in enumerate(units):
        r = 5 + i
        oc.cell(row=r, column=1, value=u)
        oc.cell(row=r, column=2, value=f"=COUNTIFS({LOG},Dashboard!{SEL},{ORG},$A{r})")
        oc.cell(row=r, column=3, value=f"=IF($B{r}=0,\"\",COUNTIFS({LOG},Dashboard!{SEL},{ORG},$A{r},{REJ},1)/$B{r})").number_format = "0.0%"
        oc.cell(row=r, column=4, value=(f"=IFERROR(COUNTIFS({LOG},Dashboard!{SEL},{ORG},$A{r},{OUTC},\"PAID\",{CYC},\">\"&Dashboard!{SLA})"
                                        f"/COUNTIFS({LOG},Dashboard!{SEL},{ORG},$A{r},{OUTC},\"PAID\"),\"\")")).number_format = "0.0%"
        oc.cell(row=r, column=5, value=f"=IF($B{r}=0,\"\",COUNTIFS({LOG},Dashboard!{SEL},{ORG},$A{r},{FTR},1)/$B{r})").number_format = "0.0%"
        oc.cell(row=r, column=6, value=f"=IFERROR(AVERAGEIFS({CYC},{LOG},Dashboard!{SEL},{ORG},$A{r},{OUTC},\"PAID\"),\"\")").number_format = "0.0"
        oc.cell(row=r, column=7, value=f"=IF($C{r}=\"\",\"\",$C{r}-Dashboard!$B$11)").number_format = "+0.0%;-0.0%;0.0%"
        oc.cell(row=r, column=8, value=f"=IF($D{r}=\"\",\"\",$D{r}-Dashboard!$B$15)").number_format = "+0.0%;-0.0%;0.0%"
        for c in range(1, 9):
            oc.cell(row=r, column=c).font = N
    oe = 4 + len(units)
    for col in "CD":
        oc.conditional_formatting.add(f"{col}5:{col}{oe}", ColorScaleRule(start_type="min", start_color="CDEFCD",
                                                                          mid_type="percentile", mid_value=50, mid_color="FDF6E3",
                                                                          end_type="max", end_color="F4C7C7"))
    for col in "GH":
        oc.conditional_formatting.add(f"{col}5:{col}{oe}", CellIsRule(operator="greaterThan", formula=["0.05"], fill=RED_F))
        oc.conditional_formatting.add(f"{col}5:{col}{oe}", CellIsRule(operator="lessThan", formula=["-0.05"], fill=GRN_F))
    for col, w in zip("ABCDEFGH", [30, 10, 12, 13, 16, 16, 16, 18]):
        oc.column_dimensions[col].width = w
    bc = BarChart()
    bc.type, bc.title, bc.height, bc.width = "bar", "SLA breach % by org unit", 9, 14
    bc.add_data(Reference(oc, min_col=4, min_row=4, max_row=oe), titles_from_data=True)
    bc.set_categories(Reference(oc, min_col=1, min_row=5, max_row=oe))
    bc.x_axis.number_format = "0%"
    oc.add_chart(bc, "J4")

    # ---------------- RAG monitor ----------------
    rm = wb.create_sheet("RAG_Monitor", 3)
    rm["A1"] = "Early-warning RAG monitor: unseen test period (claims scored at first submission)"
    rm["A1"].font = H1
    rm["A2"] = "Bands come from the model (Step 4). Cut-offs were fixed on the validation period. Problem = rejected, not paid, or paid after the SLA used in training."
    rm["A2"].font = Font(name=F, italic=True, color="808080")
    _hdr(rm, 4, ["Band", "Claims", "Share of claims", "Problem claims", "Observed problem rate", "Share of all problems"])
    for i, b in enumerate(["RED", "AMBER", "GREEN"]):
        r = 5 + i
        rm.cell(row=r, column=1, value=b).font = B
        rm.cell(row=r, column=2, value=f"=COUNTIFS({RAG},$A{r})")
        rm.cell(row=r, column=3, value=f"=$B{r}/SUM($B$5:$B$7)").number_format = "0.0%"
        rm.cell(row=r, column=4, value=f"=COUNTIFS({RAG},$A{r},{PROB},1)")
        rm.cell(row=r, column=5, value=f"=IF($B{r}=0,0,$D{r}/$B{r})").number_format = "0.0%"
        rm.cell(row=r, column=6, value=f"=$D{r}/SUM($D$5:$D$7)").number_format = "0.0%"
    for r, fill in zip((5, 6, 7), (RED_F, AMB_F, GRN_F)):
        rm.cell(row=r, column=1).fill = fill
    test_months = sorted(data.loc[data.rag != "NOT SCORED", "submit_month"].unique())
    _hdr(rm, 10, ["Month", "RED", "AMBER", "GREEN", "RED problem rate", "GREEN problem rate"])
    for i, m in enumerate(test_months):
        r = 11 + i
        rm.cell(row=r, column=1, value=pd.Timestamp(m).to_pydatetime()).number_format = "mmm yyyy"
        for j, b in enumerate(["RED", "AMBER", "GREEN"]):
            rm.cell(row=r, column=2 + j, value=f"=COUNTIFS({MON},$A{r},{RAG},\"{b}\")")
        rm.cell(row=r, column=5, value=f"=IFERROR(COUNTIFS({MON},$A{r},{RAG},\"RED\",{PROB},1)/$B{r},0)").number_format = "0.0%"
        rm.cell(row=r, column=6, value=f"=IFERROR(COUNTIFS({MON},$A{r},{RAG},\"GREEN\",{PROB},1)/$D{r},0)").number_format = "0.0%"
    re_ = 10 + len(test_months)
    for col, w in zip("ABCDEF", [12, 10, 15, 15, 20, 20]):
        rm.column_dimensions[col].width = w
    bc2 = BarChart()
    bc2.title, bc2.height, bc2.width = "Observed problem rate by RAG band", 7, 12
    bc2.add_data(Reference(rm, min_col=5, min_row=4, max_row=7), titles_from_data=True)
    bc2.set_categories(Reference(rm, min_col=1, min_row=5, max_row=7))
    bc2.y_axis.number_format = "0%"
    rm.add_chart(bc2, "H4")
    lc = LineChart()
    lc.title, lc.height, lc.width = "Problem rate per month: RED vs GREEN", 7, 12
    lc.add_data(Reference(rm, min_col=5, max_col=6, min_row=10, max_row=re_), titles_from_data=True)
    lc.set_categories(Reference(rm, min_col=1, min_row=11, max_row=re_))
    lc.y_axis.number_format = "0%"
    rm.add_chart(lc, "H19")

    # ---------------- Definitions ----------------
    df_ = wb.create_sheet("Definitions", 4)
    _hdr(df_, 1, ["KPI", "Definition", "Formula logic"])
    defs = [
        ("Claims", "Claims first submitted in the period (non-quarantined).", "COUNTIFS on Claims"),
        ("Paid", "Share of claims with a Payment Handled event.", "PAID / claims"),
        ("Rejected %", "Share of claims rejected at least once by a reviewing role (not the employee).", "Rejected=1 / claims"),
        ("Resubmitted %", "Share of claims submitted more than once (rework).", "Resubmitted=1 / claims"),
        ("First time right %", "Paid, never rejected and never resubmitted.", "FTR=1 / claims"),
        ("Mean cycle (days)", "Mean calendar days from first submission to Payment Handled, paid claims only. Medians are in the Power BI model.", "AVERAGEIFS"),
        ("SLA breach %", "Share of paid claims whose cycle exceeds the SLA threshold input. Analytical threshold, not a TU/e policy.", "COUNTIFS cycle > SLA / paid"),
        ("SLA status", "RED / AMBER / GREEN against the two editable thresholds.", "IF on thresholds"),
        ("RAG band", "Early-warning model band at first submission (test period July to December 2018 only).", "From model output"),
    ]
    for i, row in enumerate(defs, 2):
        for j, v in enumerate(row, 1):
            c = df_.cell(row=i, column=j, value=v)
            c.font, c.alignment = N, Alignment(wrap_text=True, vertical="top")
    for col, w in zip("ABC", [20, 80, 28]):
        df_.column_dimensions[col].width = w

    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if c.font is not None and c.font.name != F:
                    c.font = Font(name=F, bold=c.font.bold, italic=c.font.italic, color=c.font.color, size=c.font.size)
    wb.calculation.fullCalcOnLoad = True
    path = OUT / "excel" / "ClaimPulse_KPI_Pack.xlsx"
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    a.put("excel_claim_rows", n, "int", "ClaimPulse_KPI_Pack.xlsx")
    a.put("excel_org_units", len(units), "int", "ClaimPulse_KPI_Pack.xlsx")
    a.save(["outputs/excel/ClaimPulse_KPI_Pack.xlsx"])
    return path
