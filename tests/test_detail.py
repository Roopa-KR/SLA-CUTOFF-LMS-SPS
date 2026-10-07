"""The truck drill-down must agree with the engine (same lines, same truck membership) and explain each status."""
import pytest

from calculations.detail import truck_detail

CASES = [(13, "SH0917-06"), (13, "SH0917-01"), (4, "SH0917-02"), (16, "SH0917-05"), (13, "SH0917-10"), (18, "SH0917-10")]


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


def test_why_explains_each_kind_of_status(results):
    assert "slowest area" in detail(results, 13, "SH0917-06")[0]["why"]
    assert detail(results, 13, "SH0917-01")[0]["why"].startswith("Left complete at 18:00")
    assert "nobody is working it" in detail(results, 4, "SH0917-02")[0]["why"]
    assert "13 lines not picked" in detail(results, 16, "SH0917-05")[0]["why"]
    assert detail(results, 13, "SH0917-10")[0]["why"].startswith("No work released yet")


def test_alerts_point_to_the_bottleneck(results):
    d, _ = detail(results, 13, "SH0917-06")
    texts = [a[1] for a in d["alerts"]]
    assert any("Prescription finishes at 19:59" in x for x in texts)
    assert any("HERMERT" in x and "Idle while work is waiting" in x for x in texts)


def test_departed_truck_raises_no_operator_alerts(results):
    d, _ = detail(results, 13, "SH0917-01")
    assert d["alerts"] == []
