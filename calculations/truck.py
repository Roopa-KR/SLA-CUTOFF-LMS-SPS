"""Truck ETC (sheets TRUCK_WT_ETC and TRUCK_ETC), before operator reallocation.

For every snapshot T, truck s and work type w:
    PICK_CUTOFF(T)   = departure known at T - LOADING_MINUTES
    OPEN_LINES       = open TI102 lines of the snapshot on truck s (truck of the order as known at T), work type w
    LINES_AHEAD      = OPEN_LINES of w on all trucks whose pick cutoff is the same or earlier
    WT_ETC_FOR_TRUCK = LINES_AHEAD / ACTUALRATELINES(w)              (days, shown HH:MM)
    TRUCK_ETC        = the largest WT_ETC_FOR_TRUCK of the truck (the BOTTLENECK work type)
    READY_TIME       = T + TRUCK_ETC ; compared with PICK_CUTOFF -> SLACK and TRUCK_STATUS
Times are Excel serial numbers (days). None = NULL / cannot be calculated.
"""
from __future__ import annotations

import math

import pandas as pd

from .etc import WORKTYPES

FAR_FUTURE = 1e9


def round_half_away(x: float) -> int:
    """Excel ROUND(x, 0)."""
    return int(math.floor(abs(x) + 0.5)) * (1 if x >= 0 else -1)


EPS = 1e-9  # days (~0.1 ms): absorbs tiny float differences between Excel's stored times and Python's


def le(a: float, b: float) -> bool:
    """a <= b for Excel serial times."""
    return a <= b + EPS


def ge(a: float, b: float) -> bool:
    """a >= b for Excel serial times."""
    return a >= b - EPS


def hhmm(days) -> str:
    """Excel TEXT(days, "[h]:mm"): rounded to the nearest second, then whole minutes."""
    seconds = int(math.floor(days * 86400 + 0.5))
    minutes = seconds // 60
    return f"{minutes // 60}:{minutes % 60:02d}"


def departure_at(ship: pd.Series, t: float) -> float:
    changed = ship.DEPARTURE_CHANGED_AT
    if changed is not None and not pd.isna(changed) and ge(t, changed):
        return ship.DEPARTURE_TIME
    return ship.ORIGINAL_DEPARTURE


def _effective_truck(order_rows: pd.DataFrame, t) -> pd.Series:
    moved = order_rows.MOVED_AT.fillna(FAR_FUTURE)
    return order_rows.SHIPMENTID.where(t >= moved - EPS, order_rows.ORIGINAL_SHIPMENTID)


def _line_orders(inp) -> pd.DataFrame:
    return inp.line_map[["TI102ID", "WHORDERID"]].merge(
        inp.orders[["WHORDERID", "SHIPMENTID", "ORIGINAL_SHIPMENTID", "MOVED_AT", "RELEASE_TIME"]],
        on="WHORDERID", how="left")


def worktype_rows(inp, etc: pd.DataFrame) -> pd.DataFrame:
    """One row per snapshot x truck x work type (TRUCK_WT_ETC columns up to ADDITIONAL_OPERATORS)."""
    load_days = inp.config["LOADING_MINUTES"] / 1440
    lines = _line_orders(inp)
    ti = inp.ti[inp.ti.OPENQTY > 0].merge(lines, on="TI102ID", how="left")
    ti["TRUCK"] = _effective_truck(ti, ti.SNAPSHOT_TIME)
    open_cnt = ti.groupby(["SNAPSHOT_NO", "TRUCK", "WORKTYPE"]).size()
    etc_idx = etc.set_index(["SNAPSHOT_NO", "WORKTYPE"])
    rows = []
    for snap in inp.snapshots.itertuples():
        t = snap.T
        cut = {s.SHIPMENTID: departure_at(s, t) - load_days for s in inp.shipments.itertuples()}
        for s in inp.shipments.itertuples():
            dep = departure_at(s, t)
            for wt in WORKTYPES:
                ahead = sum(int(open_cnt.get((snap.SNAPSHOT_NO, s2, wt), 0)) for s2, c2 in cut.items() if le(c2, cut[s.SHIPMENTID]))
                e = etc_idx.loc[(snap.SNAPSHOT_NO, wt)]
                rows.append(dict(SNAPSHOT_NO=snap.SNAPSHOT_NO, T=t, SHIPMENTID=s.SHIPMENTID, WORKTYPE=wt,
                                 DEPARTURE=dep, PICK_CUTOFF=dep - load_days,
                                 OPEN_LINES=int(open_cnt.get((snap.SNAPSHOT_NO, s.SHIPMENTID, wt), 0)),
                                 LINES_AHEAD=ahead, RESOURCES=int(e.RESOURCES),
                                 ACTUALRATELINES=None if pd.isna(e.ACTUALRATELINES) else e.ACTUALRATELINES,
                                 PER_PERSON_RATE=None if pd.isna(e.PER_PERSON_RATE) else e.PER_PERSON_RATE))
    df = pd.DataFrame(rows).astype({"ACTUALRATELINES": object, "PER_PERSON_RATE": object})
    df["ACTUALRATELINES"] = df.ACTUALRATELINES.where(df.ACTUALRATELINES.notna(), None)
    df["PER_PERSON_RATE"] = df.PER_PERSON_RATE.where(df.PER_PERSON_RATE.notna(), None)
    out = [_wt_calc(r) for r in df.to_dict("records")]
    return pd.DataFrame(out)


def _wt_calc(r: dict) -> dict:
    t, cut, rate, pp = r["T"], r["PICK_CUTOFF"], r["ACTUALRATELINES"], r["PER_PERSON_RATE"]
    if r["OPEN_LINES"] == 0:
        etc_days, ready, meets = "", "", ""
    else:
        etc_days = r["LINES_AHEAD"] / rate / 24 if (rate is not None and rate > 0) else None
        ready = t + etc_days if etc_days is not None else ""
        if ge(t, cut):
            meets = "NO - CUTOFF PASSED"
        elif etc_days is not None:
            meets = "YES" if le(ready, cut) else "NO"
        else:
            meets = "UNKNOWN - NO RATE"
    if meets == "NO":
        needed = math.ceil(r["LINES_AHEAD"] / (pp * (cut - t) * 24) - 1e-9) if pp is not None else "CANNOT ESTIMATE - NO RATE"
    else:
        needed = ""
    additional = max(0, needed - r["RESOURCES"]) if isinstance(needed, int) else needed
    r.update(WT_ETC=etc_days, WT_READY_TIME=ready, WT_MEETS=meets, OPERATORS_NEEDED=needed, ADDITIONAL_OPERATORS=additional)
    return r


def _last_match(rows: pd.DataFrame, mask) -> int | None:
    hit = rows[mask]
    return int(hit.WORKTYPE.iloc[-1]) if len(hit) else None


def truck_rows(inp, wt: pd.DataFrame, after: bool = False) -> pd.DataFrame:
    """One row per snapshot x truck. With after=True, also calculate existing-operator reallocation."""
    load_days = inp.config["LOADING_MINUTES"] / 1440
    lines = _line_orders(inp)
    day = inp.config["ANALYSIS_DATE"]
    tc = inp.tc[inp.tc.TRANSACTIONDATE == day].merge(lines, on="TI102ID", how="inner")
    tc_moved = tc.MOVED_AT.fillna(FAR_FUTURE)
    picked_at = tc.groupby("TI102ID").MOVED_TO_C_AT.max()
    lifecycle = lines.copy()
    lifecycle["PICKED_AT"] = lifecycle.TI102ID.map(picked_at)
    missed_cache = {}

    def missed_counts(departure):
        """Missed-line counts for every effective truck at one departure time."""
        if departure not in missed_cache:
            effective = _effective_truck(lifecycle, departure)
            missed = ((lifecycle.RELEASE_TIME <= departure + EPS)
                      & (lifecycle.PICKED_AT.isna() | (lifecycle.PICKED_AT > departure + EPS)))
            missed_cache[departure] = effective[missed].value_counts()
        return missed_cache[departure]
    out = []
    for snap in inp.snapshots.itertuples():
        t = snap.T
        picked_by_truck = (tc[(tc.MOVED_TO_C_AT <= t + EPS) & (tc_moved > t + EPS)].groupby("ORIGINAL_SHIPMENTID").size()
                           .add(tc[(tc.MOVED_TO_C_AT <= t + EPS) & (tc_moved <= t + EPS)].groupby("SHIPMENTID").size(), fill_value=0))
        for s in inp.shipments.itertuples():
            rows = wt[(wt.SNAPSHOT_NO == snap.SNAPSHOT_NO) & (wt.SHIPMENTID == s.SHIPMENTID)]
            dep = departure_at(s, t); cut = dep - load_days
            missed_at_departure = int(missed_counts(dep).get(s.SHIPMENTID, 0))
            picked = int(picked_by_truck.get(s.SHIPMENTID, 0)); open_ = int(rows.OPEN_LINES.sum()); released = picked + open_
            etc_vals = rows.WT_ETC
            r = dict(SNAPSHOT_NO=snap.SNAPSHOT_NO, T=t, SHIPMENTID=s.SHIPMENTID, ROUTEID=s.ROUTEID, CARRIER=s.CARRIER,
                     DOCK_DOOR=s.DOCK_DOOR, ORIGINAL_DEPARTURE=s.ORIGINAL_DEPARTURE, DEPARTURE=dep, PICK_CUTOFF=cut,
                     HOURS_TO_PICK_CUTOFF=(cut - t) * 24, LINES_PICKED=picked, OPEN_LINES=open_, LINES_RELEASED=released,
                     PCT_PICKED=picked / released if released else "",
                     WORK_TYPES_WITH_OPEN_LINES=", ".join(str(w) for w, n in zip(rows.WORKTYPE, rows.OPEN_LINES) if n > 0),
                     OB_SCENARIO_ID=s.OB_SCENARIO_ID, NOTE=s.NOTE)
            r.update(_truck_answer(rows, etc_vals, t, dep, cut, open_, released, after=False,
                                   missed_at_departure=missed_at_departure))
            r.update(_truck_uncertainty(rows, r, t, cut, open_))
            if after:
                r.update(_after_reallocation(rows, r, t, cut, open_))
            out.append(r)
    return pd.DataFrame(out)


def _truck_answer(rows, etc_vals, t, dep, cut, open_, released, after, missed_at_departure=None):
    if open_ == 0:
        etc_days, bottleneck, ready = 0.0, "", ""
    elif any(v is None for v in etc_vals):
        etc_days = None
        bottleneck = f"WT {_last_match(rows, etc_vals.map(lambda v: v is None))} (no rate)"
        ready = None
    else:
        nums = [v for v in etc_vals if isinstance(v, float)]
        etc_days = max(nums)
        bottleneck = f"WT {_last_match(rows, etc_vals.map(lambda v: isinstance(v, float) and v == etc_days))}"
        ready = t + etc_days
    if ge(t, dep):
        missed = open_ if missed_at_departure is None else missed_at_departure
        status = "DEPARTED COMPLETE" if missed == 0 else f"DEPARTED - {missed} LINES LEFT BEHIND"
    elif released == 0:
        status = "NO WORK RELEASED YET"
    elif open_ == 0:
        status = "ALL PICKED"
    elif ge(t, cut):
        status = "PICK CUTOFF PASSED - LATE"
    elif etc_days is None:
        status = "NO RATE"
    else:
        status = "ON TIME" if le(ready, cut) else "AT RISK"
    meets = ("YES" if status in ("ON TIME", "ALL PICKED", "DEPARTED COMPLETE") else
             "UNKNOWN" if status == "NO RATE" else "" if status == "NO WORK RELEASED YET" else "NO")
    slack_min = round_half_away((cut - ready) * 1440) if isinstance(ready, float) and open_ > 0 and not ge(t, cut) else ""
    slack_txt = (("-" if slack_min < 0 else "+") + hhmm(abs(slack_min) / 1440)) if slack_min != "" else ""
    res = dict(TRUCK_ETC=etc_days, BOTTLENECK=bottleneck, READY_TIME=ready, TRUCK_STATUS=status,
               MEETS_DEPARTURE=meets, SLACK_MINUTES=slack_min, SLACK=slack_txt)
    if not after:
        add = ""
        if status == "AT RISK":
            w = int(bottleneck[3:4])
            add = rows[rows.WORKTYPE == w].ADDITIONAL_OPERATORS.iloc[-1]
        res["ADDITIONAL_OPERATORS_ON_BOTTLENECK"] = add
    return res


def _truck_uncertainty(rows, before: dict, t, cut, open_):
    """Aggregate work-type empirical bounds to a truck-level planning range."""
    if open_ == 0:
        low = high = early = late = ""
    else:
        lows = [v for v in rows.WT_ETC_LOW if isinstance(v, float)]
        highs = [v for v in rows.WT_ETC_HIGH if isinstance(v, float)]
        if len(lows) != len(rows[rows.OPEN_LINES > 0]) or len(highs) != len(rows[rows.OPEN_LINES > 0]):
            low = high = early = late = None
        else:
            low, high = max(lows), max(highs)
            early, late = t + low, t + high
    base = before["TRUCK_STATUS"]
    if base in ("ON TIME", "AT RISK"):
        planning = "AT RISK" if not isinstance(late, float) or not le(late, cut) else "ON TIME"
    else:
        planning = base
    gap = ""
    if planning == "AT RISK" and isinstance(high, float):
        bottleneck = rows.loc[rows.WT_ETC_HIGH.map(lambda value: isinstance(value, float) and value == high)]
        if len(bottleneck):
            gap = bottleneck.ADDITIONAL_OPERATORS_CONSERVATIVE.iloc[-1]
    planning_slack = (("-" if cut - late < -EPS else "+") + hhmm(abs(cut - late))) if isinstance(late, float) else ""
    return dict(TRUCK_ETC_LOW=low, TRUCK_ETC_HIGH=high, READY_TIME_EARLY=early, READY_TIME_LATE=late,
                PLANNING_STATUS=planning, UNCERTAINTY_CHANGES_RISK=(base == "ON TIME" and planning == "AT RISK"),
                PLANNING_OPERATOR_GAP=gap, PLANNING_SLACK=planning_slack)


def _after_reallocation(rows, before: dict, t, cut, open_):
    """The same truck after moving only qualified existing operators between work types."""
    vals = rows.WT_ETC_AFTER
    if open_ == 0:
        etc_a, ready_a, bott_a = "", "", ""
    elif any(v is None for v in vals):
        etc_a, ready_a = None, None
        bott_a = f"WT {_last_match(rows, vals.map(lambda v: v is None))} (no rate)"
    else:
        etc_a = max(v for v in vals if isinstance(v, float))
        ready_a = t + etc_a
        bott_a = f"WT {_last_match(rows, vals.map(lambda v: isinstance(v, float) and v == etc_a))}"
    status_b = before["TRUCK_STATUS"]
    if status_b in ("ON TIME", "AT RISK"):
        status_a = "ON TIME" if isinstance(ready_a, float) and le(ready_a, cut) else "AT RISK"
    else:
        status_a = status_b
    meets_a = "YES" if status_a == "ON TIME" else "NO" if status_a == "AT RISK" else before["MEETS_DEPARTURE"]
    active = isinstance(ready_a, float) and status_b in ("ON TIME", "AT RISK")
    slack_a = (("-" if cut - ready_a < -EPS else "+") + hhmm(abs(cut - ready_a))) if active else ""
    saved = before["READY_TIME"] - ready_a if active and isinstance(before["READY_TIME"], float) else ""
    parts = []
    for row in rows.itertuples():
        if row.OPEN_LINES <= 0:
            continue
        if row.REALLOCATED_IN:
            parts.append(f"WT{int(row.WORKTYPE)}:+{row.REALLOCATED_IN}")
        if row.REALLOCATED_OUT:
            parts.append(f"WT{int(row.WORKTYPE)}:-{row.REALLOCATED_OUT}")
    high_vals = [v for v in rows.WT_ETC_AFTER_HIGH if isinstance(v, float)] if "WT_ETC_AFTER_HIGH" in rows else []
    etc_high_a = max(high_vals) if high_vals else (None if open_ else "")
    ready_late_a = t + etc_high_a if isinstance(etc_high_a, float) else etc_high_a
    planning_after = ("AT RISK" if status_b in ("ON TIME", "AT RISK")
                      and (not isinstance(ready_late_a, float) or not le(ready_late_a, cut))
                      else status_a)
    planning_slack_after = (("-" if cut - ready_late_a < -EPS else "+") + hhmm(abs(cut - ready_late_a))
                            if isinstance(ready_late_a, float) else "")
    return dict(TRUCK_ETC_AFTER=etc_a, READY_TIME_AFTER=ready_a, BOTTLENECK_AFTER=bott_a, TRUCK_STATUS_AFTER=status_a,
                MEETS_DEPARTURE_AFTER=meets_a, SLACK_AFTER=slack_a, TIME_SAVED=saved,
                TRUCK_ETC_AFTER_HIGH=etc_high_a, READY_TIME_AFTER_LATE=ready_late_a,
                PLANNING_STATUS_AFTER=planning_after, PLANNING_SLACK_AFTER=planning_slack_after,
                REALLOCATIONS_ON_ITS_WORK_TYPES=", ".join(parts))
