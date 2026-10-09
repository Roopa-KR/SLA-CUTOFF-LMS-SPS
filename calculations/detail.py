"""Truck drill-down: operators, work types, orders, trends, explanation and alerts for one truck at one snapshot.

Pure pandas: works on the source tables (LM005S1, TI102, TI102C, orders, line map) and on the stored engine
results for the truck, so it can be fed from SQLite (the app) or from the workbook (tests).
Times are Excel serial days. Thresholds below are the agreed defaults (configurable in Phase 7).
"""
from __future__ import annotations

import pandas as pd

IDLE_MINUTES = 10        # assigned but not started for longer than this -> alert
NO_PICK_MINUTES = 30     # logged in without a pick for this long -> alert
LOW_PACE_PCT = 0.60      # below this share of the work type's average -> alert
MIN_HOURS_FOR_PACE = 0.5
MIN = 1 / 1440
EPS = 1e-9


def _effective_truck(df: pd.DataFrame, t: float) -> pd.Series:
    moved = df.MOVED_AT.fillna(1e9)
    return df.SHIPMENTID.where(t >= moved - EPS, df.ORIGINAL_SHIPMENTID)


def line_orders(src) -> pd.DataFrame:
    """Item -> work ID -> customer order -> truck (one work ID can hold items of several orders)."""
    lm = src.line_map
    extra = [c for c in ("ASSIGNMENTID", "WORKTYPE", "CUSTOMER") if c in lm.columns]
    ocols = [c for c in ("WHORDERID", "SHIPMENTID", "ORIGINAL_SHIPMENTID", "MOVED_AT", "STOPID", "RELEASE_TIME",
                         "OB_SCENARIO_ID", "CUSTOMER_NAME") if c in src.orders.columns]
    out = lm[["TI102ID", "WHORDERID"] + extra].merge(src.orders[ocols], on="WHORDERID", how="left")
    out["WHORDERID"] = out.WHORDERID.astype(str)
    return out


def clock(serial) -> str:
    if not isinstance(serial, (int, float)) or pd.isna(serial):
        return ""
    m = int(round((serial % 1) * 1440))
    return f"{m // 60:02d}:{m % 60:02d}"


def plural(n, word) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def dur(days) -> str:
    if not isinstance(days, (int, float)):
        return ""
    m = int(abs(days) * 1440 + 1e-6)
    return f"{m // 60}:{m % 60:02d}"


def truck_detail(src, snaps: pd.DataFrame, snapshot_no: int, ship: str, truck_hist: list[dict], wt_rows: list[dict],
                 wt_names: dict, move_rows: list[dict] | None = None) -> dict:
    """snaps: SNAPSHOT_NO, T. truck_hist: engine truck rows of this truck for all snapshots (serial times).
    wt_rows: engine work-type rows of this truck at the snapshot."""
    t = float(snaps.loc[snaps.SNAPSHOT_NO == snapshot_no, "T"].iloc[0])
    truck = next(r for r in truck_hist if r["SNAPSHOT_NO"] == snapshot_no)
    lines = line_orders(src)
    lines["TRUCK"] = _effective_truck(lines, t)
    mine = lines[lines.TRUCK == ship]
    my_ids = set(mine.TI102ID)
    day = src.config["ANALYSIS_DATE"]

    tc = src.tc[(src.tc.TRANSACTIONDATE == day)]
    missed_at_departure = _missed_at_departure(lines, tc, ship, truck["DEPARTURE"])
    picked = tc[tc.TI102ID.isin(my_ids) & (tc.MOVED_TO_C_AT <= t + EPS)]
    ti_now = src.ti[src.ti.SNAPSHOT_NO == snapshot_no]
    open_mine = ti_now[ti_now.TI102ID.isin(my_ids) & (ti_now.OPENQTY > 0)]
    lm_now = src.lm[src.lm.SNAPSHOT_NO == snapshot_no]
    open_wts = sorted(open_mine.WORKTYPE.unique().tolist())

    operators = _operators(src, t, snapshot_no, picked, open_mine, ti_now, lm_now, tc, open_wts, wt_rows)
    worktypes = _worktypes(wt_rows, picked, operators, wt_names)
    reallocations = _reallocations(wt_rows, wt_names, truck["PICK_CUTOFF"], move_rows or [])
    orders = _orders(mine, picked, open_mine, src, t, wt_names)
    workids = _workids(mine, picked, open_mine, ti_now, src, t)
    trends = _trends(src, snaps, snapshot_no, ship, truck_hist, lines, tc, t)
    kpis = dict(lines_picked=int(len(picked)), pieces_picked=float(picked.PROCESSEDQTY.sum()),
                open_lines=int(len(open_mine)), open_pieces=float(open_mine.OPENQTY.sum()),
                orders_total=len(orders), orders_done=sum(o["state"] == "Complete" for o in orders),
                workids_total=len(workids), workids_done=sum(w["state"] == "Complete" for w in workids),
                operators_worked=int(picked.RESOURCENAME.nunique()),
                shorts=int((picked.SHORTQTY > 0).sum()))
    alerts = _alerts(truck, worktypes, operators, picked, wt_names, open_wts, missed_at_departure)
    return _plain(dict(t=t, truck=truck, kpis=kpis,
                why=_why(truck, wt_rows, picked, open_mine, orders, wt_names, t, missed_at_departure),
                worktypes=worktypes, reallocations=reallocations, operators=operators,
                orders=orders, workids=workids, trends=trends, alerts=alerts))


def _missed_at_departure(lines, tc, ship, departure):
    """Lines released for this truck but not picked by its effective departure."""
    at_departure = lines.copy()
    at_departure["TRUCK"] = _effective_truck(at_departure, departure)
    eligible = at_departure[(at_departure.TRUCK == ship) & (at_departure.RELEASE_TIME <= departure + EPS)].copy()
    picked_at = tc.groupby("TI102ID").MOVED_TO_C_AT.max()
    eligible["PICKED_AT"] = eligible.TI102ID.map(picked_at)
    return eligible[eligible.PICKED_AT.isna() | (eligible.PICKED_AT > departure + EPS)]


def _plain(x):
    """numpy scalars -> plain Python, so templates and JSON work everywhere."""
    if isinstance(x, dict):
        return {k: _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    if hasattr(x, "item") and not isinstance(x, (str, bytes)):
        try:
            return x.item()
        except (ValueError, AttributeError):
            return x
    return x


# ------------------------------------------------------------------ operators
def _operators(src, t, snap, picked, open_mine, ti_now, lm_now, tc, open_wts, wt_rows):
    rate_by_wt = {r["WORKTYPE"]: r["PER_PERSON_RATE"] for r in wt_rows}
    relevant = set(picked.RESOURCENAME.dropna()) | set(open_mine.RESOURCENAME.dropna())
    relevant |= set(lm_now[(lm_now.WIPIND == "Y") & lm_now.WORKTYPE.isin(open_wts)].RESOURCENAME)
    unassigned_by_wt = ti_now[(ti_now.CURRENTSTATUSID == 0) & (ti_now.OPENQTY > 0)].groupby("WORKTYPE").size()
    done_all = tc[tc.MOVED_TO_C_AT <= t + EPS]
    out = []
    for op in sorted(relevant):
        seg = lm_now[lm_now.RESOURCENAME == op]
        if seg.empty:
            continue
        cur = seg.sort_values("STARTDATETIME").iloc[-1]
        wt = int(cur.WORKTYPE)
        logged_in = bool((seg.WIPIND == "Y").any())
        login = seg[seg.WIPIND == "Y"].STARTDATETIME.max() if logged_in else None
        held = ti_now[(ti_now.RESOURCENAME == op) & (ti_now.CURRENTSTATUSID >= 1) & (ti_now.OPENQTY > 0)]
        if not logged_in:
            status = "Logged out"
        elif (held.CURRENTSTATUSID == 2).any():
            status = "Working"
        elif len(held):
            status = "Assigned, not started"
        else:
            status = "Idle"
        mine_op = picked[picked.RESOURCENAME == op]
        all_op = done_all[done_all.RESOURCENAME == op]
        hours_wt = seg[seg.WORKTYPE == wt].TOTALTIMEINSEC.sum() / 3600
        lines_wt = int((all_op.WORKTYPE == wt).sum())
        pace = lines_wt / hours_wt if hours_wt > 0 and lines_wt > 0 else None
        avg = rate_by_wt.get(wt)
        last_pick = all_op.MOVED_TO_C_AT.max() if len(all_op) else None
        since = (t - max(x for x in (last_pick, login) if x is not None and not pd.isna(x))) / MIN if logged_in else None
        finish = t + len(held) / pace / 24 if pace and len(held) else None
        flags = []
        if status == "Idle" and unassigned_by_wt.get(wt, 0) > 0:
            flags.append("Idle while work is waiting")
        if status == "Assigned, not started":
            waited = (t - held.CHANGEDATETIME.min()) / MIN
            if waited > IDLE_MINUTES:
                flags.append(f"Assigned {waited:.0f} min, not started")
        if logged_in and since is not None and since >= NO_PICK_MINUTES:
            flags.append(f"No pick for {since:.0f} min")
        if not logged_in and len(held):
            flags.append("Logged out holding work")
        if logged_in and pace and avg and hours_wt >= MIN_HOURS_FOR_PACE and pace < LOW_PACE_PCT * avg:
            flags.append("Low pace")
        out.append(dict(name=op, worktype=wt, status=status, login=login, lines_truck=int(len(mine_op)),
                        pieces_truck=float(mine_op.PROCESSEDQTY.sum()), lines_today=int(len(all_op)),
                        hours_wt=hours_wt, pace=pace, wt_avg=avg, pct_of_avg=(pace / avg) if pace and avg else None,
                        shorts=int((mine_op.SHORTQTY > 0).sum()), held=int(len(held)), finish=finish,
                        since_pick=since, flags=flags))
    order = {"Working": 0, "Assigned, not started": 1, "Idle": 2, "Logged out": 3}
    return sorted(out, key=lambda o: (order[o["status"]], -o["lines_truck"], o["name"]))


# ------------------------------------------------------------------ work types
def _worktypes(wt_rows, picked, operators, wt_names):
    out = []
    for r in wt_rows:
        w = r["WORKTYPE"]
        ops = [o for o in operators if o["worktype"] == w]
        done = int((picked.WORKTYPE == w).sum())
        if r["OPEN_LINES"] == 0 and done == 0:
            continue
        out.append(dict(worktype=w, name=wt_names.get(w, ""), open_lines=r["OPEN_LINES"], ahead=r["LINES_AHEAD"],
                        ahead_other=r["LINES_AHEAD"] - r["OPEN_LINES"], picked=done,
                        pct=done / (done + r["OPEN_LINES"]) if done + r["OPEN_LINES"] else None,
                        working=sum(o["status"] == "Working" for o in ops),
                        assigned=sum(o["status"] == "Assigned, not started" for o in ops),
                        idle=sum(o["status"] == "Idle" for o in ops), resources=r["RESOURCES"],
                        rate=r["ACTUALRATELINES"], per_person=r["PER_PERSON_RATE"], etc=r["WT_ETC"],
                        ready=r["WT_READY_TIME"], meets=r["WT_MEETS"], status=r["WT_STATUS"],
                        rate_low=r.get("RATE_LOW"), rate_high=r.get("RATE_HIGH"),
                        etc_low=r.get("WT_ETC_LOW"), etc_high=r.get("WT_ETC_HIGH"),
                        ready_early=r.get("WT_READY_EARLY"), ready_late=r.get("WT_READY_LATE"),
                        planning_meets=r.get("WT_PLANNING_MEETS", r["WT_MEETS"]),
                        planning_status=r.get("WT_PLANNING_STATUS", r["WT_STATUS"]),
                        uncertainty_evidence=r.get("UNCERTAINTY_EVIDENCE", ""),
                        additional=r["ADDITIONAL_OPERATORS"], resources_after=r["RESOURCES_AFTER_REALLOCATION"],
                        additional_conservative=r.get("ADDITIONAL_OPERATORS_CONSERVATIVE", r["ADDITIONAL_OPERATORS"]),
                        rate_after=r["RATE_AFTER_REALLOCATION"], reallocated_in=r["REALLOCATED_IN"],
                        reallocated_out=r["REALLOCATED_OUT"], ready_after=r["WT_READY_TIME_AFTER"],
                        meets_after=r["WT_MEETS_AFTER"], status_after=r["WT_STATUS_AFTER"],
                        etc_after_low=r.get("WT_ETC_AFTER_LOW"), etc_after_high=r.get("WT_ETC_AFTER_HIGH"),
                        ready_after_late=r.get("WT_READY_AFTER_LATE"),
                        planning_status_after=r.get("WT_PLANNING_STATUS_AFTER", r["WT_STATUS_AFTER"]),
                        no_eligible=r["NO_ELIGIBLE_OPERATOR"]))
    return out


def _reallocations(wt_rows, wt_names, cutoff, move_rows):
    """Separate decision table; the existing Details tables are left unchanged."""
    out = []
    for r in wt_rows:
        if r.get("WT_PLANNING_STATUS", r["WT_STATUS"]) != "AT RISK" and not r["REALLOCATED_IN"]:
            continue
        before, after = r["WT_ETC"], r["WT_ETC_AFTER"]
        familiarity = r["FAMILIARITY_EVIDENCE"] or ""
        eligible_candidates = r.get("ELIGIBLE_CANDIDATES", "") or ""
        candidate_parts = eligible_candidates.split("); ") if eligible_candidates else []
        candidate_parts = [part if part.endswith(")") else part + ")" for part in candidate_parts]
        selected_moves = [m for m in move_rows if m.get("destination_worktype") == r["WORKTYPE"]]
        required = r.get("OPERATORS_NEEDED_CONSERVATIVE", "")
        remaining = max(0, int(required) - int(r["RESOURCES_AFTER_REALLOCATION"])) if isinstance(required, (int, float)) else ""
        out.append(dict(worktype=r["WORKTYPE"], name=wt_names.get(r["WORKTYPE"], ""),
                        operators_before=r["RESOURCES"], etc_before=before, ready_before=r["WT_READY_TIME"],
                        operator=r["REALLOCATED_IN"], source=(r["REALLOCATION_REASON"] or ""),
                        eligibility=r["REALLOCATION_REASON"] or "",
                        familiarity=familiarity,
                        familiarity_parts=[part.strip() for part in familiarity.split(";") if part.strip()],
                        operators_after=r["RESOURCES_AFTER_REALLOCATION"], rate_before=r["ACTUALRATELINES"],
                        rate_after=r["RATE_AFTER_REALLOCATION"], etc_after=after,
                        ready_after=r["WT_READY_TIME_AFTER"], cutoff=cutoff,
                        time_saved=(before - after if isinstance(before, float) and isinstance(after, float) else ""),
                        status_before=r.get("WT_PLANNING_STATUS", r["WT_STATUS"]),
                        status_after=r.get("WT_PLANNING_STATUS_AFTER", r["WT_STATUS_AFTER"]),
                        etc_low=r.get("WT_ETC_LOW"), etc_high=r.get("WT_ETC_HIGH"),
                        etc_after_low=r.get("WT_ETC_AFTER_LOW"), etc_after_high=r.get("WT_ETC_AFTER_HIGH"),
                        allocated=len(selected_moves), remaining_shortfall=remaining,
                        uncertainty_evidence=r.get("UNCERTAINTY_EVIDENCE", ""),
                        eligible_candidates=eligible_candidates,
                        eligible_candidate_list=candidate_parts,
                        moves=selected_moves,
                        no_eligible=r["NO_ELIGIBLE_OPERATOR"] or ""))
    return out


# ------------------------------------------------------------------ orders
def _orders(mine, picked, open_mine, src, t, wt_names):
    out = []
    to_order = dict(zip(mine.TI102ID, mine.WHORDERID))
    done = picked.groupby(picked.TI102ID.map(to_order)).size()
    opn = open_mine.groupby(open_mine.TI102ID.map(to_order)).size()
    wts = src.line_map.groupby("WHORDERID").WORKTYPE.agg(lambda s: ", ".join(str(w) for w in sorted(s.unique())))
    for oid, g in mine.groupby("WHORDERID"):
        o = g.iloc[0]
        n_done, n_open, total = int(done.get(oid, 0)), int(opn.get(oid, 0)), len(g)
        if o.RELEASE_TIME > t + EPS:
            state = "Not released"
        elif n_open == 0 and n_done > 0:
            state = "Complete"
        elif n_done == 0:
            state = "Not started"
        else:
            state = "In progress"
        flags = []
        if not pd.isna(o.MOVED_AT):
            flags.append(f"Moved from {o.ORIGINAL_SHIPMENTID} at {clock(o.MOVED_AT)}")
        if isinstance(o.OB_SCENARIO_ID, str) and "T03" in o.OB_SCENARIO_ID:
            flags.append("Added late")
        if isinstance(o.OB_SCENARIO_ID, str) and "T06" in o.OB_SCENARIO_ID:
            flags.append("Has cut / cancelled lines")
        name = o.CUSTOMER_NAME if "CUSTOMER_NAME" in g and isinstance(o.CUSTOMER_NAME, str) else ""
        out.append(dict(order=str(oid), customer=name, stop=o.STOPID, release=o.RELEASE_TIME, worktypes=wts.get(oid, ""),
                        workids=int(g.ASSIGNMENTID.nunique()) if "ASSIGNMENTID" in g else None,
                        lines=total, done=n_done, open=n_open, state=state, flags=flags))
    rank = {"In progress": 0, "Not started": 1, "Not released": 2, "Complete": 3}
    return sorted(out, key=lambda o: (rank[o["state"]], o["release"], o["order"]))


def _workids(mine, picked, open_mine, ti_now, src, t):
    """Work IDs (assignments) carrying this truck's items: region, customers, progress, picker."""
    if "ASSIGNMENTID" not in mine:
        return []
    names = {}
    if "CUSTOMER_NAME" in mine:
        names = dict(zip(mine.WHORDERID, mine.CUSTOMER_NAME))
    picked_ids, open_ids = set(picked.TI102ID), set(open_mine.TI102ID)
    holder = open_mine.dropna(subset=["RESOURCENAME"]).set_index("TI102ID").RESOURCENAME.to_dict()
    pick_by = picked.set_index("TI102ID").RESOURCENAME.to_dict()
    out = []
    for wid, g in mine.groupby("ASSIGNMENTID"):
        ids = list(g.TI102ID)
        n_done = sum(i in picked_ids for i in ids)
        n_open = sum(i in open_ids for i in ids)
        released = n_done + n_open
        started = any(i in holder for i in ids)
        if released == 0:
            state = "Not released"
        elif n_open == 0:
            state = "Complete"
        elif started or n_done:
            state = "In progress"
        else:
            state = "Waiting"
        people = sorted({holder[i] for i in ids if i in holder} | {pick_by[i] for i in ids if i in pick_by})
        orders = sorted(g.WHORDERID.unique())
        custs = sorted({names.get(o) for o in orders if isinstance(names.get(o), str)})
        out.append(dict(workid=int(wid), worktype=int(g.WORKTYPE.iloc[0]), orders=len(orders),
                        customers=", ".join(custs[:3]) + (f" +{len(custs) - 3}" if len(custs) > 3 else ""),
                        several=len(orders) > 1, items=len(ids), done=n_done, open=n_open, state=state,
                        pickers=", ".join(people)))
    rank = {"In progress": 0, "Waiting": 1, "Not released": 2, "Complete": 3}
    return sorted(out, key=lambda w: (rank[w["state"]], w["worktype"], w["workid"]))


# ------------------------------------------------------------------ trends over the snapshots
def _trends(src, snaps, snapshot_no, ship, truck_hist, lines, tc, t):
    upto = snaps[snaps.SNAPSHOT_NO <= snapshot_no]
    hist = {r["SNAPSHOT_NO"]: r for r in truck_hist}
    labels = [clock(x) for x in upto["T"]]
    my_ids = set(lines[lines.TRUCK == ship].TI102ID)
    mine = tc[tc.TI102ID.isin(my_ids)]
    edges = [-1e12] + list(upto["T"])
    through = {}
    for w in sorted(mine.WORKTYPE.unique()):
        mw = mine[mine.WORKTYPE == w]
        through[int(w)] = [int(((mw.MOVED_TO_C_AT > a + EPS) & (mw.MOVED_TO_C_AT <= b + EPS)).sum()) for a, b in zip(edges, edges[1:])]
    lm = src.lm[src.lm.WIPIND == "Y"]
    pickers = {int(w): [int(lm[(lm.SNAPSHOT_NO == n) & (lm.WORKTYPE == w)].RESOURCENAME.nunique()) for n in upto.SNAPSHOT_NO]
               for w in through}
    return dict(labels=labels,
                open_lines=[hist[n]["OPEN_LINES"] for n in upto.SNAPSHOT_NO],
                picked=[hist[n]["LINES_PICKED"] for n in upto.SNAPSHOT_NO],
                slack=[hist[n]["SLACK_MINUTES"] if hist[n]["SLACK_MINUTES"] != "" else None for n in upto.SNAPSHOT_NO],
                status=[hist[n]["TRUCK_STATUS"] for n in upto.SNAPSHOT_NO],
                throughput=through, pickers=pickers)


# ------------------------------------------------------------------ explanation
def _why(truck, wt_rows, picked, open_mine, orders, wt_names, t, missed_at_departure):
    st, cut, dep = truck["TRUCK_STATUS"], truck["PICK_CUTOFF"], truck["DEPARTURE"]
    name = lambda w: wt_names.get(w, f"WT {w}")
    if st == "ON TIME" and truck.get("PLANNING_STATUS") == "AT RISK":
        ranged = [r for r in wt_rows if isinstance(r.get("WT_ETC_HIGH"), float)]
        controlling = max(ranged, key=lambda r: r["WT_ETC_HIGH"]) if ranged else None
        if controlling:
            return (f"The point estimate is on time, but measured operator productivity varies. "
                    f"{name(controlling['WORKTYPE'])} has an empirical ETC range of "
                    f"{dur(controlling['WT_ETC_LOW'])} to {dur(controlling['WT_ETC_HIGH'])}, with the slower case "
                    f"ready at {clock(controlling['WT_READY_LATE'])} versus the {clock(cut)} cutoff. "
                    "This range uses observed paces and excludes unmeasured travel, congestion, breaks and equipment delays.")
    if st == "DEPARTED COMPLETE":
        if len(picked):
            last = picked.MOVED_TO_C_AT.max()
            when = (f"{dur(cut - last)} before the pick cutoff ({clock(cut)})" if last <= cut
                    else f"{dur(last - cut)} after the pick cutoff, inside the loading buffer")
            return (f"Left complete at {clock(dep)}. Last line picked at {clock(last)}, {when}. "
                    f"{plural(len(picked), 'line')} picked by {plural(picked.RESOURCENAME.nunique(), 'operator')}.")
        return f"Left at {clock(dep)}."
    if st.startswith("DEPARTED - "):
        by = missed_at_departure.groupby("WORKTYPE").size()
        parts = ", ".join(f"{n} {name(w)}" for w, n in by.items())
        count = len(missed_at_departure)
        detail = f" ({parts})" if parts else ""
        return f"Left at {clock(dep)} with {count} lines not picked{detail}. They missed this truck."
    if st == "NO WORK RELEASED YET":
        first = min((o["release"] for o in orders), default=None)
        return f"No work released yet for this truck." + (f" Its first orders are released at {clock(first)}." if first else "")
    if st == "ALL PICKED":
        text = f"All {truck['LINES_RELEASED']} released lines are picked; the truck waits for departure at {clock(dep)}."
        later = [o for o in orders if o["state"] == "Not released"]
        if later:
            text += (f" {plural(len(later), 'more order')} for this truck {'is' if len(later) == 1 else 'are'} not released yet "
                     f"(next at {clock(min(o['release'] for o in later))}).")
        return text
    late = [r for r in wt_rows if r["OPEN_LINES"] > 0]
    if st == "NO RATE":
        nr = [r for r in late if r["WT_ETC"] is None]
        def reason(r):
            if r["RESOURCES"] == 0:
                return (f"{name(r['WORKTYPE'])} has {plural(r['OPEN_LINES'], 'open line')} for this truck but nobody "
                        "is logged in there, so its speed and the truck's ETC cannot be estimated.")
            return (f"{name(r['WORKTYPE'])} has {plural(r['OPEN_LINES'], 'open line')} and "
                    f"{plural(r['RESOURCES'], 'picker')} logged in, but no positive observed rate yet, so the truck's "
                    "ETC cannot be estimated.")
        return " ".join(reason(r) for r in nr) + \
            " Someone needs to start picking there."
    b = int(truck["BOTTLENECK"][3:4]) if truck["BOTTLENECK"] else None
    br = next((r for r in wt_rows if r["WORKTYPE"] == b), None)
    if br is None:
        return ""
    ahead = br["LINES_AHEAD"] - br["OPEN_LINES"]
    text = (f"{name(b)} is the slowest area: {br['LINES_AHEAD']} lines to pick before this truck is ready "
            f"({br['OPEN_LINES']} of its own" + (f" + {ahead} for trucks leaving earlier" if ahead else "") + "). "
            f"{plural(br['RESOURCES'], 'picker')} at {br['ACTUALRATELINES']:.0f} lines/h {'needs' if br['RESOURCES'] == 1 else 'need'} {dur(br['WT_ETC'])}, so it is ready at "
            f"{clock(br['WT_READY_TIME'])}")
    if st == "PICK CUTOFF PASSED - LATE":
        return (f"The pick cutoff ({clock(cut)}) has passed with {truck['OPEN_LINES']} lines still open. " + text +
                f"; departure is {clock(dep)}.")
    others = [r for r in late if r["WORKTYPE"] != b and r["WT_READY_TIME"] not in ("", None)]
    other_txt = "; ".join(f"{name(r['WORKTYPE'])} is ready at {clock(r['WT_READY_TIME'])}" for r in others)
    if st == "ON TIME":
        return text + f", {dur(cut - truck['READY_TIME'])} before the pick cutoff ({clock(cut)})." + (f" {other_txt}." if other_txt else "")
    need = br["OPERATORS_NEEDED"]
    text += f", but picking must finish by {clock(cut)} ({truck['SLACK']})."
    if other_txt:
        text += f" {other_txt}."
    if isinstance(need, int):
        text += f" To make it, {name(b)} needs {need} pickers ({br['ADDITIONAL_OPERATORS']} more)."
    if br["REALLOCATED_IN"]:
        res = "meets" if truck["TRUCK_STATUS_AFTER"] == "ON TIME" else "still misses"
        text += (f" After reallocating existing operator(s) {br['REALLOCATED_IN']}, the truck {res} the cutoff "
                 f"({truck['SLACK_AFTER']}).")
    elif br["NO_ELIGIBLE_OPERATOR"]:
        text += " " + br["NO_ELIGIBLE_OPERATOR"]
    return text


# ------------------------------------------------------------------ alerts and actions
def _alerts(truck, worktypes, operators, picked, wt_names, open_wts, missed_at_departure):
    alerts = []
    st = truck.get("PLANNING_STATUS") or truck["TRUCK_STATUS"]
    for w in worktypes:
        if w["planning_meets"] == "NO":
            act = (f"Reallocate {w['reallocated_in']} to {w['name']}" if w["reallocated_in"]
                   else w["no_eligible"] or "Review qualified existing operators")
            if w["meets"] == "NO":
                message = f"{w['name']} finishes at {clock(w['ready'])}, after the pick cutoff."
            else:
                finish = w["ready_late"] if isinstance(w["ready_late"], float) else w["ready"]
                message = f"{w['name']} conservative finish is {clock(finish)}, after the pick cutoff."
            alerts.append(("danger", message, act))
        if w["meets"] == "UNKNOWN - NO RATE":
            if w["resources"] == 0:
                text, action = (f"{w['name']} has {w['open_lines']} open lines and nobody logged in.",
                                f"Send a picker to {w['name']}.")
            else:
                text, action = (f"{w['name']} has {w['open_lines']} open lines but no positive observed rate yet.",
                                "Check activity and complete initial picks to establish a rate.")
            alerts.append(("warning", text, action))
        if w["ahead_other"] > 0 and w["planning_meets"] == "NO":
            alerts.append(("info", f"{w['ahead_other']} {w['name']} lines of earlier trucks are picked first.",
                           "Check whether this truck should be prioritised over an earlier one."))
    for o in operators:
        if o["worktype"] not in open_wts:      # only people on areas this truck still needs
            continue
        for f in o["flags"]:
            action = {"Idle while work is waiting": "Give this picker work",
                      "Logged out holding work": "Reassign the work this person holds",
                      "Low pace": "Check for problems (location, equipment, training)"}.get(f, "Check on the floor")
            if f.startswith("Assigned"):
                action = "Ask the picker to start"
            alerts.append(("warning", f"{o['name']} ({wt_names.get(o['worktype'], '')}): {f}.", action))
    if len(picked):
        shorts = picked[picked.SHORTQTY > 0].groupby("SRCLOC").size()
        for loc, n in shorts[shorts >= 2].items():
            alerts.append(("warning", f"{n} short picks at location {loc}.", "Check stock / replenish the location."))
    if st in ("AT RISK",) and truck["TRUCK_STATUS_AFTER"] == "AT RISK":
        reason = next((w.get("no_eligible") for w in worktypes if w.get("no_eligible")), "")
        if not reason:  # the per-work-type alert already shows a no-eligible explanation
            alerts.append(("danger", "Still at risk after safe reallocation checks.",
                           "Review departure or order priorities; do not force an unsafe move."))
    if st.startswith("DEPARTED - "):
        alerts.append(("danger", f"{len(missed_at_departure)} lines missed this truck.",
                       "Move them to the next truck or ship separately."))
    return alerts
