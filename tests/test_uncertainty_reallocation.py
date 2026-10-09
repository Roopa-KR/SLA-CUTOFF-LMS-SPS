"""Warehouse-realistic uncertainty and iterative reallocation scenarios."""
from types import SimpleNamespace

import pandas as pd

from calculations.reallocation import apply_reallocation
from calculations.uncertainty import add_uncertainty

T = 1.0
H = 1 / 24


def _inputs(operators, familiar=True):
    lm, completed = [], []
    for name, worktype, pace in operators:
        lm.append(dict(SNAPSHOT_NO=1, RESOURCENAME=name, WORKTYPE=worktype, WIPIND="Y",
                       STARTDATETIME=T - H, TOTALTIMEINSEC=3600))
        for _ in range(pace):
            completed.append(dict(RESOURCENAME=name, WORKTYPE=worktype,
                                  TRANSACTIONDATE=T, MOVED_TO_C_AT=T - 0.01))
    experience = ([dict(RESOURCENAME=name, WORKTYPE=2, EXPERIENCE_LEVEL="Cross-trained")
                   for name, _, _ in operators] if familiar else [])
    return SimpleNamespace(
        lm=pd.DataFrame(lm), tc=pd.DataFrame(completed), config={"ANALYSIS_DATE": T},
        snapshots=pd.DataFrame([dict(SNAPSHOT_NO=1, T=T)]),
        operator_experience=pd.DataFrame(experience,
                                         columns=["RESOURCENAME", "WORKTYPE", "EXPERIENCE_LEVEL"]))


def _row(ship, wt, open_lines, ahead, resources, rate, cutoff=T + H):
    etc = ahead / rate / 24 if open_lines and rate else ("" if not open_lines else None)
    ready = T + etc if isinstance(etc, float) else etc
    meets = "" if not open_lines else "YES" if ready <= cutoff else "NO"
    return dict(SNAPSHOT_NO=1, T=T, SHIPMENTID=ship, WORKTYPE=wt, PICK_CUTOFF=cutoff,
                OPEN_LINES=open_lines, LINES_AHEAD=ahead, RESOURCES=resources,
                ACTUALRATELINES=rate, PER_PERSON_RATE=(rate / resources if resources else None),
                WT_ETC=etc, WT_READY_TIME=ready, WT_MEETS=meets)


def test_uncertain_productivity_turns_tight_point_forecast_into_planning_risk():
    inp = _inputs([("slow", 2, 5), ("fast", 2, 15)])
    wt = pd.DataFrame([_row("DEST", 2, 15, 15, 2, 20)])

    row = add_uncertainty(inp, wt).iloc[0]

    assert row.WT_ETC == 0.75 * H
    assert row.WT_ETC_LOW == 0.5 * H
    assert row.WT_ETC_HIGH == 1.5 * H
    assert row.WT_MEETS == "YES" and row.WT_PLANNING_STATUS == "AT RISK"
    assert row.OBSERVED_PACE_COUNT == 2
    assert "not a probabilistic confidence interval" in row.UNCERTAINTY_EVIDENCE


def test_six_operator_gap_is_filled_iteratively_from_multiple_completed_sources():
    operators = [(f"a{i}", 1, 10) for i in range(3)] + [(f"b{i}", 3, 10) for i in range(3)]
    inp = _inputs(operators)
    wt = pd.DataFrame([
        _row("SOURCE1", 1, 0, 0, 3, 30),
        _row("DEST", 2, 80, 80, 2, 20),
        _row("SOURCE3", 3, 0, 0, 3, 30),
    ])

    result, summary, moves = apply_reallocation(inp, wt, {1: "Source A", 2: "Destination", 3: "Source B"})
    dest = result[result.WORKTYPE == 2].iloc[0]

    assert len(moves) == 6
    assert set(moves.SOURCE_WORKTYPE) == {1, 3}
    assert dest.RESOURCES_AFTER_REALLOCATION == 8
    assert dest.WT_PLANNING_STATUS_AFTER == "ON TIME"
    assert summary.iloc[0].ALLOCATED_OPERATORS == 6
    assert summary.iloc[0].REMAINING_SHORTFALL == 0
    assert result.drop_duplicates("WORKTYPE").RESOURCES.sum() == result.drop_duplicates("WORKTYPE").RESOURCES_AFTER_REALLOCATION.sum()


def test_shortfall_is_reported_when_only_three_qualified_operators_exist():
    inp = _inputs([(f"a{i}", 1, 10) for i in range(3)])
    wt = pd.DataFrame([_row("SOURCE", 1, 0, 0, 3, 30), _row("DEST", 2, 80, 80, 2, 20)])

    result, summary, moves = apply_reallocation(inp, wt, {1: "Source", 2: "Destination"})
    dest = result[result.WORKTYPE == 2].iloc[0]

    assert len(moves) == 3
    assert dest.WT_PLANNING_STATUS_AFTER == "AT RISK"
    assert summary.iloc[0].ALLOCATED_OPERATORS == 3
    assert summary.iloc[0].REMAINING_SHORTFALL == 3
    assert "No active operator" in summary.iloc[0].NO_ELIGIBLE_REASON


def test_positive_point_slack_does_not_make_a_source_operator_spare():
    inp = _inputs([("only", 1, 10)])
    wt = pd.DataFrame([
        _row("SOURCE", 1, 5, 5, 1, 10, cutoff=T + 2 * H),
        _row("DEST", 2, 20, 20, 1, 10),
    ])

    result, summary, moves = apply_reallocation(inp, wt, {1: "Source", 2: "Destination"})

    assert moves.empty
    assert result[result.WORKTYPE == 1].iloc[0].WT_PLANNING_STATUS_AFTER == "ON TIME"
    assert result[result.WORKTYPE == 2].iloc[0].WT_PLANNING_STATUS_AFTER == "AT RISK"
    assert "source productivity" in summary.iloc[0].NO_ELIGIBLE_REASON


def test_selected_move_carries_its_own_source_impact_rows():
    inp = _inputs([("safe", 1, 10), ("unsafe", 3, 10)])
    wt = pd.DataFrame([
        _row("SAFE-SOURCE", 1, 0, 0, 1, 10),
        _row("DEST", 2, 20, 20, 1, 10),
        _row("UNSAFE-SOURCE", 3, 9, 9, 1, 10),
    ])

    _, _, moves = apply_reallocation(inp, wt, {1: "Safe", 2: "Destination", 3: "Unsafe"})

    assert len(moves) == 1
    assert moves.iloc[0].OPERATOR == "safe"
    assert moves.iloc[0].SOURCE_SHIPMENTID == "No remaining source work"
