"""The Python engine must reproduce every Truck ETC value of the Excel workbook (the golden reference)."""
import pytest

from parity import mismatches

ETC_CALC = {"S1: TOTALTIMEINHRS": "TOTALTIMEINHRS", "S2: TOTALLINES": "TOTALLINES", "S2: TOTALPIECES": "TOTALPIECES",
            "RESOURCES": "RESOURCES", "S3: ACTUALRATELINES": "ACTUALRATELINES", "S3: ACTUALRATEPIECES": "ACTUALRATEPIECES",
            "S4: OPENLINES": "OPENLINES", "S4: OPENQTY": "OPENQTY", "S5: ETCLINES (hours)": "ETCLINES",
            "S5: ETCPIECES (hours)": "ETCPIECES"}

TRUCK_WT_ETC = {"DEPARTURE_AT_SNAPSHOT": "DEPARTURE", "PICK_CUTOFF_AT_SNAPSHOT": "PICK_CUTOFF",
                "OPEN_LINES (this truck)": "OPEN_LINES", "LINES_AHEAD (incl. this truck)": "LINES_AHEAD",
                "RESOURCES (ETC_CALC)": "RESOURCES", "ACTUALRATELINES (ETC_CALC)": "ACTUALRATELINES",
                "WT_ETC_FOR_TRUCK (HH:MM)": "WT_ETC", "WT_READY_TIME (before adding)": "WT_READY_TIME",
                "WT_MEETS_TRUCK_CUTOFF": "WT_MEETS", "PER_PERSON_RATE": "PER_PERSON_RATE",
                "OPERATORS_NEEDED": "OPERATORS_NEEDED", "ADDITIONAL_OPERATORS": "ADDITIONAL_OPERATORS",
                "TEMPS_ADDED_TO_THIS_WORKTYPE": "TEMPS_ADDED", "TEMP_OPERATORS_ADDED": "TEMP_OPERATORS_ADDED",
                "WT_ETC_AFTER_ADDING (HH:MM)": "WT_ETC_AFTER", "WT_READY_TIME (after adding)": "WT_READY_TIME_AFTER",
                "WT_MEETS_AFTER_ADDING": "WT_MEETS_AFTER", "TIME_SAVED (HH:MM)": "WT_TIME_SAVED"}

TRUCK_ETC = {"DEPARTURE_AT_SNAPSHOT": "DEPARTURE", "PICK_CUTOFF_AT_SNAPSHOT": "PICK_CUTOFF",
             "HOURS_TO_PICK_CUTOFF": "HOURS_TO_PICK_CUTOFF", "LINES_PICKED": "LINES_PICKED", "OPEN_LINES": "OPEN_LINES",
             "LINES_RELEASED": "LINES_RELEASED", "PCT_PICKED": "PCT_PICKED",
             "WORK_TYPES_WITH_OPEN_LINES": "WORK_TYPES_WITH_OPEN_LINES", "BOTTLENECK_WORKTYPE": "BOTTLENECK",
             "READY_TIME": "READY_TIME", "TRUCK_STATUS": "TRUCK_STATUS", "MEETS_DEPARTURE": "MEETS_DEPARTURE",
             "SLACK_MINUTES": "SLACK_MINUTES", "SLACK (h:mm)": "SLACK",
             "ADDITIONAL_OPERATORS_ON_BOTTLENECK": "ADDITIONAL_OPERATORS_ON_BOTTLENECK",
             "TRUCK_ETC_AFTER_ADDING (HH:MM)": "TRUCK_ETC_AFTER", "READY_TIME_AFTER_ADDING": "READY_TIME_AFTER",
             "BOTTLENECK_AFTER_ADDING": "BOTTLENECK_AFTER", "TRUCK_STATUS_AFTER_ADDING": "TRUCK_STATUS_AFTER",
             "MEETS_DEPARTURE_AFTER_ADDING": "MEETS_DEPARTURE_AFTER", "SLACK_AFTER_ADDING (h:mm)": "SLACK_AFTER",
             "TIME_SAVED (HH:MM)": "TIME_SAVED", "TEMPS_ADDED_ON_ITS_WORK_TYPES": "TEMPS_ADDED_ON_ITS_WORK_TYPES"}

TEMP_NEED = {"TRUCKS_NOT_MEETING_CUTOFF": "TRUCKS_NOT_MEETING", "TEMPS_NEEDED": "TEMPS_NEEDED",
             "EARLIEST_CUTOFF_AT_RISK": "EARLIEST_CUTOFF_AT_RISK", "TEMPS_ADDED": "TEMPS_ADDED",
             "TEMP_OPERATORS_ADDED": "TEMP_OPERATORS_ADDED", "SHORTFALL_TEMPS": "SHORTFALL_TEMPS"}


def _check(ref, res, mapping):
    for excel_col, py_col in mapping.items():
        n, examples = mismatches(ref[excel_col], res[py_col])
        assert n == 0, f"{excel_col}: {n} rows differ, e.g. (row, excel, python) {examples}"


def test_row_order_matches(results, reference):
    assert list(reference["TRUCK_ETC"].SHIPMENTID) == list(results.trucks.SHIPMENTID)
    assert list(reference["TRUCK_WT_ETC"].WORKTYPE) == list(results.worktypes.WORKTYPE)


@pytest.mark.parametrize("col", list(ETC_CALC))
def test_etc_calc(results, reference, col):
    _check(reference["ETC_CALC"], results.etc, {col: ETC_CALC[col]})


@pytest.mark.parametrize("col", list(TRUCK_WT_ETC))
def test_truck_wt_etc(results, reference, col):
    _check(reference["TRUCK_WT_ETC"], results.worktypes, {col: TRUCK_WT_ETC[col]})


@pytest.mark.parametrize("col", list(TRUCK_ETC))
def test_truck_etc(results, reference, col):
    _check(reference["TRUCK_ETC"], results.trucks, {col: TRUCK_ETC[col]})


def test_truck_etc_hours(results, reference):
    excel = reference["TRUCK_ETC"]["TRUCK_ETC (h)"]
    python = [v * 24 if isinstance(v, float) else v for v in results.trucks.TRUCK_ETC]
    n, ex = mismatches(excel, python)
    assert n == 0, ex


@pytest.mark.parametrize("col", list(TEMP_NEED))
def test_temp_need(results, reference, col):
    _check(reference["TRUCK_TEMP_NEED"], results.temp_need, {col: TEMP_NEED[col]})
