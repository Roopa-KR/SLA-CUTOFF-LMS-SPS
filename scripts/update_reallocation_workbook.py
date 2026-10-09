"""Replace the synthetic temporary-operator workbook model with real-operator reallocation outputs."""
from __future__ import annotations

from copy import copy
import datetime as dt
from pathlib import Path
import sys

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.workbook.defined_name import DefinedName

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from calculations.engine import run
from calculations.excel_io import serial_to_datetime


PATH = "data/workbook.xlsx"


def excel_value(value, time_kind=False):
    if value is None or value == "":
        return None
    if time_kind and isinstance(value, (int, float)):
        return serial_to_datetime(float(value))
    return value


def duration(value):
    return dt.timedelta(days=float(value)) if isinstance(value, (int, float)) else None


def style_header(ws, columns):
    for column in range(1, columns + 1):
        cell = ws.cell(5, column)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    ws.freeze_panes = "A6"
    ws.auto_filter.ref = f"A5:{openpyxl.utils.get_column_letter(columns)}{max(5, ws.max_row)}"


results = run(PATH)
wb = openpyxl.load_workbook(PATH)

for name in ("TRUCK_TEMP_NEED", "TRUCK_TEMP_LM005S1", "TEMP_OPERATORS"):
    if name in wb.sheetnames:
        del wb[name]

# Remove names tied to the deleted synthetic pool and replace the after-model names.
for name in list(wb.defined_names):
    if name.startswith(("TEMP_", "TN_", "TT_")) or name in {"DA_TEMPS", "TE_TEMPS"}:
        del wb.defined_names[name]
for name in ("DA_ETC", "DA_READY", "DA_MEETS", "DA_SAVED"):
    if name in wb.defined_names:
        del wb.defined_names[name]
for name, target in {
    "DR_STATUS": "TRUCK_WT_ETC!$Q$6:$Q$3569",
    "DR_IN": "TRUCK_WT_ETC!$R$6:$R$3569",
    "DR_RESOURCES": "TRUCK_WT_ETC!$S$6:$S$3569",
    "DR_RATE": "TRUCK_WT_ETC!$T$6:$T$3569",
    "DA_ETC": "TRUCK_WT_ETC!$U$6:$U$3569",
    "DA_READY": "TRUCK_WT_ETC!$V$6:$V$3569",
    "DA_STATUS": "TRUCK_WT_ETC!$W$6:$W$3569",
    "DA_SAVED": "TRUCK_WT_ETC!$X$6:$X$3569",
    "DR_CANDIDATES": "TRUCK_WT_ETC!$Y$6:$Y$3569",
    "DR_NO_ELIGIBLE": "TRUCK_WT_ETC!$Z$6:$Z$3569",
    "TE_REALLOCATIONS": "TRUCK_ETC!$AH$6:$AH$599",
}.items():
    wb.defined_names.add(DefinedName(name, attr_text=target))

cfg = wb["TRUCK_CONFIG"]
cfg["A8"] = "REALLOCATION_EVIDENCE_RULE"
cfg["B8"] = "ACTUAL_METRICS_WITH_LABELLED_SIMULATED_FAMILIARITY"
cfg["C8"] = ("An operator may qualify through actual destination history or the explicitly simulated familiarity sheet. "
             "All productivity inputs remain actual or are visibly labelled estimates; the source must remain on time.")
cfg["D8"] = "BUSINESS RULE"
cfg["A9"] = "UNCERTAINTY_RULE"
cfg["B9"] = "EMPIRICAL_OBSERVED_PACE_ENVELOPE"
cfg["C9"] = ("Point ETC is retained. Low/high ETC use the highest/lowest positive operator pace actually observed "
             "for that work type by the snapshot, scaled to current resources and enclosing the point team rate. "
             "This is a sensitivity range, not a probabilistic confidence interval; unmeasured delays are excluded.")
cfg["D9"] = "CALCULATION RULE"

readme = wb["TRUCK_README"]
readme["A2"] = ("Work, pickers, sessions, work IDs, orders and routes are actual SMD data. Truck departures and dock doors "
                 "are synthesized. OPERATOR_EXPERIENCE is a simulated planning assumption, clearly separated from actual "
                 "LM005S1 and TI102C records. Reallocation never creates temporary operators.")
readme["B7"] = ("Departure per route = last item release + 60 min, rounded up to the next quarter hour (= pick cutoff), "
                 "+ 30 min loading. Familiarity is simulated and labelled; performance records are not synthesized.")
readme["B8"] = ("TRUCK_WT_ETC compares each work type ready time with the truck pick cutoff and marks it AT RISK when ready "
                 "time or the slower empirical ETC bound exceeds cutoff. Reallocation checks both point and conservative "
                 "source capacity. Destination contribution is actual destination pace when available, otherwise "
                 "MIN(actual source pace, actual destination per-person rate).")
readme["B10"] = ("TRUCK_VIEW, TRUCK_CONFIG, TRUCK_ETC, TRUCK_WT_ETC, TRUCK_REALLOCATION, "
                  "TRUCK_REALLOCATION_MOVES, OPERATOR_EXPERIENCE, TRUCK_CHECKS, outbound/source link sheets, ETC_CALC, CONFIG, LM005S1, "
                  "TI102, TI102C, M150, M123, M123T.")

# Preserve columns A:P (the existing work-type detail) and append/replace only the old what-if block.
ws = wb["TRUCK_WT_ETC"]
headers = ["WT_STATUS", "REALLOCATED_IN", "OPERATORS_AFTER_REALLOCATION", "RATE_AFTER_REALLOCATION",
           "WT_ETC_AFTER_REALLOCATION (HH:MM)", "WT_READY_TIME_AFTER_REALLOCATION",
           "WT_STATUS_AFTER_REALLOCATION", "TIME_SAVED_AFTER_REALLOCATION (HH:MM)",
           "ELIGIBLE_CANDIDATES", "NO_ELIGIBLE_OPERATOR", "DATA_TYPE"]
for col, header in enumerate(headers, 17):
    ws.cell(5, col, header)
    ws.cell(5, col)._style = copy(ws.cell(5, 16)._style)
for row_no, record in enumerate(results.worktypes.to_dict("records"), 6):
    values = [record["WT_STATUS"], record["REALLOCATED_IN"], record["RESOURCES_AFTER_REALLOCATION"],
              record["RATE_AFTER_REALLOCATION"], duration(record["WT_ETC_AFTER"]),
              excel_value(record["WT_READY_TIME_AFTER"], True), record["WT_STATUS_AFTER"],
              duration(record["WT_TIME_SAVED"]), record["ELIGIBLE_CANDIDATES"],
              record["NO_ELIGIBLE_OPERATOR"], "CALCULATED"]
    for col, value in enumerate(values, 17):
        ws.cell(row_no, col).value = value
    ws.cell(row_no, 19).number_format = "0"
    ws.cell(row_no, 20).number_format = "0.00"
    ws.cell(row_no, 21).number_format = "[h]:mm"
    ws.cell(row_no, 22).number_format = "hh:mm"
    ws.cell(row_no, 24).number_format = "[h]:mm"

# Append uncertainty fields; columns A:P and the existing reallocation block remain unchanged.
uncertainty_headers = ["OBSERVED_PACE_COUNT", "RATE_LOW", "RATE_HIGH", "WT_ETC_LOW", "WT_ETC_HIGH",
                       "WT_READY_EARLY", "WT_READY_LATE", "WT_PLANNING_STATUS",
                       "OPERATORS_NEEDED_CONSERVATIVE", "ADDITIONAL_OPERATORS_CONSERVATIVE",
                       "RATE_AFTER_LOW", "RATE_AFTER_HIGH", "WT_ETC_AFTER_LOW", "WT_ETC_AFTER_HIGH",
                       "WT_READY_AFTER_LATE", "WT_PLANNING_STATUS_AFTER", "UNCERTAINTY_EVIDENCE"]
for col, header in enumerate(uncertainty_headers, 28):
    ws.cell(5, col, header)
    ws.cell(5, col)._style = copy(ws.cell(5, 16)._style)
for row_no, record in enumerate(results.worktypes.to_dict("records"), 6):
    for offset, header in enumerate(uncertainty_headers, 28):
        value = record[header]
        if header in {"WT_READY_EARLY", "WT_READY_LATE", "WT_READY_AFTER_LATE"}:
            value = excel_value(value, True)
        elif header in {"WT_ETC_LOW", "WT_ETC_HIGH", "WT_ETC_AFTER_LOW", "WT_ETC_AFTER_HIGH"}:
            value = duration(value)
        ws.cell(row_no, offset).value = value

truck = wb["TRUCK_ETC"]
truck_headers = ["TRUCK_ETC_AFTER_REALLOCATION (HH:MM)", "READY_TIME_AFTER_REALLOCATION",
                 "BOTTLENECK_AFTER_REALLOCATION", "TRUCK_STATUS_AFTER_REALLOCATION",
                 "MEETS_DEPARTURE_AFTER_REALLOCATION", "SLACK_AFTER_REALLOCATION (h:mm)",
                 "TIME_SAVED_AFTER_REALLOCATION (HH:MM)", "REALLOCATIONS_ON_ITS_WORK_TYPES"]
for col, header in enumerate(truck_headers, 27):
    truck.cell(5, col, header)
for row_no, record in enumerate(results.trucks.to_dict("records"), 6):
    values = [duration(record["TRUCK_ETC_AFTER"]), excel_value(record["READY_TIME_AFTER"], True),
              record["BOTTLENECK_AFTER"], record["TRUCK_STATUS_AFTER"], record["MEETS_DEPARTURE_AFTER"],
              record["SLACK_AFTER"], duration(record["TIME_SAVED"]), record["REALLOCATIONS_ON_ITS_WORK_TYPES"]]
    for col, value in enumerate(values, 27):
        truck.cell(row_no, col).value = value
    truck.cell(row_no, 27).number_format = "[h]:mm"
    truck.cell(row_no, 28).number_format = "hh:mm"
    truck.cell(row_no, 33).number_format = "[h]:mm"

truck_uncertainty_headers = ["TRUCK_ETC_LOW", "TRUCK_ETC_HIGH", "READY_TIME_EARLY", "READY_TIME_LATE",
                             "PLANNING_STATUS", "PLANNING_OPERATOR_GAP", "PLANNING_SLACK", "TRUCK_ETC_AFTER_HIGH",
                             "READY_TIME_AFTER_LATE", "PLANNING_STATUS_AFTER", "PLANNING_SLACK_AFTER"]
for col, header in enumerate(truck_uncertainty_headers, 36):
    truck.cell(5, col, header)
for row_no, record in enumerate(results.trucks.to_dict("records"), 6):
    for col, header in enumerate(truck_uncertainty_headers, 36):
        value = record[header]
        if header in {"READY_TIME_EARLY", "READY_TIME_LATE", "READY_TIME_AFTER_LATE"}:
            value = excel_value(value, True)
        elif header in {"TRUCK_ETC_LOW", "TRUCK_ETC_HIGH", "TRUCK_ETC_AFTER_HIGH"}:
            value = duration(value)
        truck.cell(row_no, col).value = value

for sheet_name in ("TRUCK_REALLOCATION", "TRUCK_REALLOCATION_MOVES"):
    if sheet_name in wb.sheetnames:
        del wb[sheet_name]

summary = wb.create_sheet("TRUCK_REALLOCATION", 5)
summary["A1"] = "AT-RISK WORK TYPES AND EXISTING-OPERATOR REALLOCATION"
summary["A1"].font = Font(bold=True, size=14, color="FFFFFF")
summary["A1"].fill = PatternFill("solid", fgColor="1F4E78")
summary.merge_cells("A1:U1")
summary["A2"] = ("No temporary or new operators. A blank REALLOCATED_OPERATORS plus NO_ELIGIBLE_REASON means the move was "
                 "not forced because the source data contains no defensible qualified candidate.")
summary.merge_cells("A2:U2")
summary["A3"] = ("ETC = LINES_AHEAD / RATE / 24. Destination rate after = team rate before + operator's observed destination "
                 "pace. Source rate after = team rate before - operator's observed source pace; every source cutoff must remain safe.")
summary.merge_cells("A3:U3")
summary_headers = list(results.reallocation.columns)
for col, header in enumerate(summary_headers, 1):
    summary.cell(5, col, header)
for row_no, record in enumerate(results.reallocation.to_dict("records"), 6):
    for col, header in enumerate(summary_headers, 1):
        value = record[header]
        if header in {"T", "READY_BEFORE", "READY_AFTER", "PICK_CUTOFF"}:
            value = excel_value(value, True)
        elif header in {"ETC_BEFORE", "ETC_AFTER", "TIME_SAVED"}:
            value = duration(value)
        summary.cell(row_no, col).value = value
style_header(summary, len(summary_headers))
for row in summary.iter_rows(min_row=6):
    for cell in row:
        if summary_headers[cell.column - 1] in {"T", "READY_BEFORE", "READY_AFTER", "PICK_CUTOFF"}:
            cell.number_format = "hh:mm"
        elif summary_headers[cell.column - 1] in {"ETC_BEFORE", "ETC_AFTER", "TIME_SAVED"}:
            cell.number_format = "[h]:mm"

moves = wb.create_sheet("TRUCK_REALLOCATION_MOVES", 6)
moves["A1"] = "ACCEPTED EXISTING-OPERATOR MOVES"
moves["A1"].font = Font(bold=True, size=14, color="FFFFFF")
moves["A1"].fill = PatternFill("solid", fgColor="1F4E78")
moves["A2"] = "Every row is a real headcount-neutral move supported by prior destination completions and observed labor time."
move_headers = list(results.moves.columns) if len(results.moves.columns) else [
    "SNAPSHOT_NO", "T", "DEST_WORKTYPE", "DESTINATION", "OPERATOR", "SOURCE_WORKTYPE", "SOURCE",
    "ELIGIBILITY_REASON", "FAMILIARITY_EVIDENCE", "SOURCE_RATE_BEFORE", "SOURCE_OPERATOR_PACE",
    "SOURCE_RATE_AFTER", "SOURCE_MIN_SLACK_AFTER", "DEST_RATE_BEFORE", "OPERATOR_DEST_PACE", "DEST_RATE_AFTER",
    "ETC_BEFORE", "READY_BEFORE", "ETC_AFTER", "READY_AFTER", "PICK_CUTOFF", "TIME_SAVED", "STATUS_AFTER"]
for col, header in enumerate(move_headers, 1):
    moves.cell(5, col, header)
for row_no, record in enumerate(results.moves.to_dict("records"), 6):
    for col, header in enumerate(move_headers, 1):
        value = record[header]
        if header in {"T", "READY_BEFORE", "READY_AFTER", "PICK_CUTOFF"}:
            value = excel_value(value, True)
        elif header in {"ETC_BEFORE", "ETC_AFTER", "TIME_SAVED"}:
            value = duration(value)
        moves.cell(row_no, col).value = value
style_header(moves, len(move_headers))

checks = wb["TRUCK_CHECKS"]
check_rows = [
    (14, "WORKFORCE", "No synthesized operators", "Temporary names found in LM005S1", '=COUNTIF(LM_RES,"TEMP_*")', "0"),
    (15, "REALLOCATION", "Every accepted move has familiarity evidence", "Accepted rows with blank evidence", '=COUNTIFS(TRUCK_REALLOCATION_MOVES!$E$6:$E$1000,"?*",TRUCK_REALLOCATION_MOVES!$I$6:$I$1000,"")', "0"),
    (16, "REALLOCATION", "Every source remains safe", "Accepted moves with negative source slack", '=COUNTIF(TRUCK_REALLOCATION_MOVES!$M$6:$M$1000,"<0")', "0"),
    (17, "WORKFORCE", "Total operator count is conserved", "Sum operators before - after across work-type rows", '=SUM(TRUCK_WT_ETC!$I$6:$I$3569)-SUM(TRUCK_WT_ETC!$S$6:$S$3569)', "0"),
    (18, "REALLOCATION", "Reallocation never slows a destination", "Reallocated destinations with negative time saved", '=COUNTIFS(TRUCK_WT_ETC!$R$6:$R$3569,"?*",TRUCK_WT_ETC!$X$6:$X$3569,"<0")', "0"),
    (19, "REALLOCATION", "Safe work never becomes at risk", "YES before and AT RISK after", '=COUNTIFS(TRUCK_WT_ETC!$M$6:$M$3569,"YES",TRUCK_WT_ETC!$W$6:$W$3569,"AT RISK")', "0"),
    (20, "RESULT", "Truck-snapshot rows AT RISK before reallocation", "TRUCK_STATUS = AT RISK", '=COUNTIF(TE_STATUS,"AT RISK")', "info"),
    (21, "RESULT", "Truck-snapshot rows AT RISK after reallocation", "TRUCK_STATUS_AFTER_REALLOCATION = AT RISK", '=COUNTIF(TE_STATA,"AT RISK")', "info"),
    (22, "RESULT", "Truck-snapshot rows rescued", "Before AT RISK, after ON TIME", '=COUNTIFS(TE_STATUS,"AT RISK",TE_STATA,"ON TIME")', "info"),
    (23, "RESULT", "Trucks departed with items left behind", "TRUCK_STATUS starts with DEPARTED -", '=COUNTIF(TE_STATUS,"DEPARTED - *")', "info"),
    (24, "RESULT", "Accepted existing-operator moves", "Rows in TRUCK_REALLOCATION_MOVES", '=COUNTIF(TRUCK_REALLOCATION_MOVES!$E$6:$E$1000,"?*")', "info"),
    (25, "RESULT", "At-risk work types without an eligible operator", "Rows with NO_ELIGIBLE_REASON", '=COUNTIF(TRUCK_REALLOCATION!$V$6:$V$1000,"?*")', "info"),
]
for row, area, check, rule, formula, expected in check_rows:
    checks.cell(row, 1, area); checks.cell(row, 2, check); checks.cell(row, 3, rule)
    checks.cell(row, 4, formula); checks.cell(row, 5, expected)
    checks.cell(row, 6, f'=IF(E{row}="info","INFO",IF(D{row}=VALUE(E{row}),"PASS","FAIL"))')

view = wb["TRUCK_VIEW"]
view_headers = {17: "READY_TIME_AFTER_REALLOCATION", 18: "STATUS_AFTER_REALLOCATION",
                19: "TIME_SAVED (HH:MM)", 20: "REALLOCATIONS"}
for col, value in view_headers.items():
    view.cell(8, col, value)
for row in range(9, 42):
    view.cell(row, 20, f'=INDEX(TE_REALLOCATIONS,MATCH(1,INDEX((TE_SNAP=TV_SNAPSHOT_NO)*(TE_SHIP=$A{row}),0),0))')
detail_headers = {8: "WT_READY_TIME (before reallocation)", 11: "REALLOCATED_IN",
                  12: "NO_ELIGIBLE_OPERATOR", 13: "WT_READY_TIME_AFTER_REALLOCATION",
                  14: "WT_STATUS_AFTER_REALLOCATION"}
for col, value in detail_headers.items():
    view.cell(49, col, value)
for row in range(50, 56):
    match = f'MATCH(1,INDEX((D_SNAP=TV_SNAPSHOT_NO)*(D_SHIP=TV_SHIPMENTID)*(D_WT=$A{row}),0),0)'
    view.cell(row, 11, f'=INDEX(DR_IN,{match})')
    view.cell(row, 12, f'=INDEX(DR_NO_ELIGIBLE,{match})')
    view.cell(row, 14, f'=INDEX(DA_STATUS,{match})')

# Update visible prose without touching formulas or the source/detail columns.
replacements = {"temporary operators": "existing-operator reallocation",
                "temporary operator": "existing operator",
                "after adding": "after reallocation",
                "After adding": "After reallocation"}
for ws_text in wb.worksheets:
    for row in ws_text.iter_rows():
        for cell in row:
            if isinstance(cell.value, str) and not cell.value.startswith("="):
                value = cell.value
                for old, new in replacements.items():
                    value = value.replace(old, new)
                cell.value = value

wb.calculation.fullCalcOnLoad = True
wb.calculation.forceFullCalc = True
wb.calculation.calcMode = "auto"
wb.save(PATH)
