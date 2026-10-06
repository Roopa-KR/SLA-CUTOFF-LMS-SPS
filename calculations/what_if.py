"""What-if: temporary operators (sheets TRUCK_TEMP_NEED, TRUCK_TEMP_LM005S1, after-adding columns).

Temporary operators are never added to the workforce data (LM005S1); they only change this calculation.
    TEMPS_NEEDED(T, w) = largest ADDITIONAL_OPERATORS among the trucks that work type w makes late
    Allocation: each temporary operator, in pool order, goes to the work type they can work that still
                needs people and whose earliest at-risk truck has the earliest pick cutoff
    WT_ETC_AFTER       = LINES_AHEAD / (PER_PERSON_RATE x (RESOURCES + TEMPS_ADDED x TEMP_PACE_PCT))
"""
from __future__ import annotations

import pandas as pd

from .etc import WORKTYPES
from .truck import ge, le


def temp_need(wt: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (snap, w), g in wt.groupby(["SNAPSHOT_NO", "WORKTYPE"], sort=True):
        late = g[g.WT_MEETS == "NO"]
        nums = [v for v in late.ADDITIONAL_OPERATORS if isinstance(v, int)]
        rows.append(dict(SNAPSHOT_NO=snap, WORKTYPE=w, TRUCKS_NOT_MEETING=len(late),
                         TEMPS_NEEDED=(max(nums) if nums else 0) if len(late) else 0,
                         EARLIEST_CUTOFF_AT_RISK=late.PICK_CUTOFF.min() if len(late) else None))
    return pd.DataFrame(rows)


def allocate_temps(need: pd.DataFrame, temps: pd.DataFrame) -> pd.DataFrame:
    """One row per snapshot x temporary operator with the work type they are given (None = not needed)."""
    out = []
    idx = need.set_index(["SNAPSHOT_NO", "WORKTYPE"])
    for snap in sorted(need.SNAPSHOT_NO.unique()):
        given = {w: 0 for w in WORKTYPES}
        for t in temps.itertuples(index=False):
            best, best_w = None, None
            for w in WORKTYPES:
                n = idx.loc[(snap, w)]
                if getattr(t, f"_{w}") == 1 and n.TEMPS_NEEDED > given[w]:
                    if best is None or n.EARLIEST_CUTOFF_AT_RISK < best:
                        best, best_w = n.EARLIEST_CUTOFF_AT_RISK, w
            if best_w is not None:
                given[best_w] += 1
            out.append(dict(SNAPSHOT_NO=snap, RESOURCENAME=t.RESOURCENAME, WORKTYPE=best_w))
    return pd.DataFrame(out)


def apply_temps(wt: pd.DataFrame, need: pd.DataFrame, grid: pd.DataFrame, pace: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    assigned = grid.dropna(subset=["WORKTYPE"])
    added = assigned.groupby(["SNAPSHOT_NO", "WORKTYPE"]).size()
    names = assigned.groupby(["SNAPSHOT_NO", "WORKTYPE"]).RESOURCENAME.agg(", ".join)
    need = need.copy()
    need["TEMPS_ADDED"] = [int(added.get((r.SNAPSHOT_NO, r.WORKTYPE), 0)) for r in need.itertuples()]
    need["TEMP_OPERATORS_ADDED"] = [names.get((r.SNAPSHOT_NO, r.WORKTYPE), "") for r in need.itertuples()]
    need["SHORTFALL_TEMPS"] = (need.TEMPS_NEEDED - need.TEMPS_ADDED).clip(lower=0)
    wt = wt.copy()
    key = list(zip(wt.SNAPSHOT_NO, wt.WORKTYPE))
    wt["TEMPS_ADDED"] = [int(added.get(k, 0)) for k in key]
    wt["TEMP_OPERATORS_ADDED"] = [names.get(k, "") for k in key]
    after, ready, meets, saved = [], [], [], []
    for r in wt.itertuples():
        if r.OPEN_LINES == 0:
            a = ""
        elif r.TEMPS_ADDED == 0 or r.PER_PERSON_RATE is None:
            a = r.WT_ETC
        else:
            a = r.LINES_AHEAD / (r.PER_PERSON_RATE * (r.RESOURCES + r.TEMPS_ADDED * pace)) / 24
        rd = r.T + a if isinstance(a, float) else ""
        if r.OPEN_LINES == 0:
            m = ""
        elif ge(r.T, r.PICK_CUTOFF):
            m = "NO - CUTOFF PASSED"
        elif isinstance(a, float):
            m = "YES" if le(rd, r.PICK_CUTOFF) else "NO"
        else:
            m = "UNKNOWN - NO RATE"
        after.append(a); ready.append(rd); meets.append(m)
        saved.append(r.WT_ETC - a if r.TEMPS_ADDED > 0 and isinstance(r.WT_ETC, float) and isinstance(a, float) else "")
    wt["WT_ETC_AFTER"], wt["WT_READY_TIME_AFTER"], wt["WT_MEETS_AFTER"], wt["WT_TIME_SAVED"] = after, ready, meets, saved
    return wt, need
