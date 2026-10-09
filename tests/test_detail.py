"""The truck drill-down must agree with the engine (same items, same truck membership) and explain each status.
Cases are real trucks of 08-Sep-2026."""
import pytest

from calculations.detail import truck_detail

CASES = [(9, "SH0908-1042"), (1, "SH0908-1888"), (10, "SH0908-1981"), (12, "SH0908-1027"),
         (1, "SH0908-1027"), (7, "SH0908-1018"), (6, "SH0908-1010"), (9, "SH0908-1981")]


def detail(results, snap, ship):
    hist = results.trucks[results.trucks.SHIPMENTID == ship].to_dict("records")
    wt = results.worktypes[(results.worktypes.SNAPSHOT_NO == snap) & (results.worktypes.SHIPMENTID == ship)].to_dict("records")
    return truck_detail(results.inputs, results.snapshots, snap, ship, hist, wt, results.worktype_names), \
        next(h for h in hist if h["SNAPSHOT_NO"] == snap)


@pytest.mark.parametrize("snap,ship", CASES)
def test_kpis_reconcile_with_engine(results, snap, ship):
    d, t = detail(results, snap, ship)
    assert d["kpis"]["lines_picked"] == t["LINES_PICKED"]
    assert d["kpis"]["open_lines"] == t["OPEN_LINES"]
    assert sum(sum(v) for v in d["trends"]["throughput"].values()) == t["LINES_PICKED"]
    assert sum(o["lines_truck"] for o in d["operators"]) == t["LINES_PICKED"]
    assert sum(w["open_lines"] for w in d["worktypes"]) == t["OPEN_LINES"]
    assert sum(w["items"] for w in d["workids"]) == sum(o["lines"] for o in d["orders"])


def test_why_explains_each_kind_of_status(results):
    assert "OTC is the slowest area" in detail(results, 6, "SH0908-1010")[0]["why"]
    assert "nobody is logged in there" in detail(results, 1, "SH0908-1888")[0]["why"]
    assert "1 picker logged in, but no positive observed rate yet" in detail(results, 6, "SH0908-1027")[0]["why"]
    assert detail(results, 12, "SH0908-1027")[0]["why"].startswith("Left complete at 18:30")
    assert detail(results, 1, "SH0908-1027")[0]["why"].startswith("No work released yet")
    assert "not released yet" in detail(results, 7, "SH0908-1018")[0]["why"]


def test_dataset_has_one_labelled_cutoff_passed_example_and_no_late_departures(results):
    statuses = results.trucks.TRUCK_STATUS
    assert not statuses.str.startswith("DEPARTED -").any()
    late = results.trucks[statuses == "PICK CUTOFF PASSED - LATE"]
    assert list(zip(late.SNAPSHOT_NO, late.SHIPMENTID)) == [(11, "SH0908-1072")]
    assert "The pick cutoff (18:45) has passed" in detail(results, 11, "SH0908-1072")[0]["why"]
    assert not set(results.trucks.SHIPMENTID) & {"SH0908-1001", "SH0908-1075", "SH0908-1085"}


def test_one_truck_has_many_work_ids_and_orders(results):
    d, _ = detail(results, 9, "SH0908-1042")
    assert d["kpis"]["workids_total"] == 71 and d["kpis"]["orders_total"] == 47


def test_alerts_point_to_the_bottleneck(results):
    d, _ = detail(results, 6, "SH0908-1010")
    texts = [a[1] for a in d["alerts"]]
    assert any("OTC finishes at 20:29" in x for x in texts)


def test_reallocation_long_text_is_split_for_responsive_display(results):
    d, _ = detail(results, 10, "SH0908-1981")
    row = d["reallocations"][0]
    assert len(row["eligible_candidate_list"]) == 2
    assert all(candidate.endswith(")") for candidate in row["eligible_candidate_list"])
    assert row["eligible_candidate_list"][0].startswith("jmccraw (WT 6;")
    assert row["eligible_candidate_list"][-1].startswith("hgroff (WT 6;")
    assert row["familiarity_parts"] == [
        "Simulated familiarity: Cross-trained - working knowledge",
        "not verified history. Estimated destination contribution = min(actual source pace 16.5, actual destination per-person rate 25.2) = 16.5 lines/h",
    ]


def test_departed_complete_truck_raises_no_alerts(results):
    d, _ = detail(results, 12, "SH0908-1027")
    assert d["alerts"] == []
