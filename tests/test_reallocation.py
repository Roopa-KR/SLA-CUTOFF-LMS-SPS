"""Existing-operator reallocation must conserve headcount and use observed cross-training."""
from types import SimpleNamespace

import pandas as pd

from calculations.reallocation import apply_reallocation


T = 1.0


def _inputs(include_destination_history=True):
    lm = pd.DataFrame([
        dict(SNAPSHOT_NO=1, RESOURCENAME="alice", WORKTYPE=1, WIPIND="Y", STARTDATETIME=T - 1/24,
             TOTALTIMEINSEC=3600),
        dict(SNAPSHOT_NO=1, RESOURCENAME="bob", WORKTYPE=1, WIPIND="Y", STARTDATETIME=T - 1/24,
             TOTALTIMEINSEC=3600),
        dict(SNAPSHOT_NO=1, RESOURCENAME="alice", WORKTYPE=2, WIPIND="N", STARTDATETIME=T - 2/24,
             TOTALTIMEINSEC=3600),
    ])
    completed = []
    for i in range(5):
        completed.append(dict(RESOURCENAME="alice", WORKTYPE=1, TRANSACTIONDATE=T, MOVED_TO_C_AT=T - 0.01))
    for i in range(15):
        completed.append(dict(RESOURCENAME="bob", WORKTYPE=1, TRANSACTIONDATE=T, MOVED_TO_C_AT=T - 0.01))
    if include_destination_history:
        for i in range(10):
            completed.append(dict(RESOURCENAME="alice", WORKTYPE=2, TRANSACTIONDATE=T, MOVED_TO_C_AT=T - 0.02))
    return SimpleNamespace(lm=lm, tc=pd.DataFrame(completed), config={"ANALYSIS_DATE": T},
                           operator_experience=pd.DataFrame(columns=["RESOURCENAME", "WORKTYPE", "EXPERIENCE_LEVEL"]))


def _worktypes():
    return pd.DataFrame([
        dict(SNAPSHOT_NO=1, T=T, SHIPMENTID="SOURCE", WORKTYPE=1, PICK_CUTOFF=T + 0.20,
             OPEN_LINES=1, LINES_AHEAD=1, RESOURCES=2, ACTUALRATELINES=20.0,
             PER_PERSON_RATE=10.0, WT_ETC=1/20/24, WT_READY_TIME=T + 1/20/24, WT_MEETS="YES"),
        dict(SNAPSHOT_NO=1, T=T, SHIPMENTID="DEST", WORKTYPE=2, PICK_CUTOFF=T + 0.06,
             OPEN_LINES=10, LINES_AHEAD=10, RESOURCES=1, ACTUALRATELINES=5.0,
             PER_PERSON_RATE=5.0, WT_ETC=10/5/24, WT_READY_TIME=T + 10/5/24, WT_MEETS="NO"),
    ])


def test_reallocation_uses_observed_operator_pace_and_conserves_headcount():
    result, summary, moves = apply_reallocation(_inputs(), _worktypes(), {1: "Source", 2: "Destination"})
    source = result[result.WORKTYPE == 1].iloc[0]
    dest = result[result.WORKTYPE == 2].iloc[0]

    assert len(moves) == 1 and moves.iloc[0].OPERATOR == "alice"
    assert moves.iloc[0].OPERATOR_DEST_PACE == 10.0
    assert source.RATE_AFTER_REALLOCATION == 15.0       # 20 team rate - Alice's observed 5 lines/h
    assert dest.RATE_AFTER_REALLOCATION == 15.0         # 5 team rate + Alice's observed 10 lines/h
    assert source.WT_STATUS_AFTER == "ON TIME"
    assert dest.WT_STATUS == "AT RISK" and dest.WT_STATUS_AFTER == "ON TIME"
    assert source.RESOURCES_AFTER_REALLOCATION + dest.RESOURCES_AFTER_REALLOCATION == 3
    assert summary.iloc[0].TIME_SAVED > 0
    assert "10 completed Destination lines" in moves.iloc[0].FAMILIARITY_EVIDENCE


def test_operator_without_destination_evidence_is_not_moved():
    result, summary, moves = apply_reallocation(
        _inputs(include_destination_history=False), _worktypes(), {1: "Source", 2: "Destination"})
    dest = result[result.WORKTYPE == 2].iloc[0]

    assert moves.empty
    assert dest.RESOURCES_AFTER_REALLOCATION == dest.RESOURCES
    assert dest.WT_ETC_AFTER == dest.WT_ETC
    assert dest.WT_STATUS_AFTER == "AT RISK"
    assert "no available source operator has actual or simulated Destination familiarity" in summary.iloc[0].NO_ELIGIBLE_REASON


def test_simulated_familiarity_is_labelled_and_uses_data_bounded_estimate():
    inp = _inputs(include_destination_history=False)
    inp.operator_experience = pd.DataFrame([
        dict(RESOURCENAME="alice", WORKTYPE=2, EXPERIENCE_LEVEL="Cross-trained - working knowledge")])
    result, _, moves = apply_reallocation(inp, _worktypes(), {1: "Source", 2: "Destination"})

    move = moves.iloc[0]
    assert move.OPERATOR == "alice"
    assert move.FAMILIARITY_TYPE == "SIMULATED PLANNING ASSUMPTION"
    assert move.OPERATOR_DEST_PACE == 5.0  # min(actual source pace 5, actual destination average 5)
    assert "not verified history" in move.FAMILIARITY_EVIDENCE
    assert result[result.WORKTYPE == 2].iloc[0].WT_STATUS_AFTER == "ON TIME"
