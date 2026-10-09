"""Reallocate existing, demonstrably cross-trained operators to at-risk work types.

No workforce is created by this module.  At each snapshot an operator may move
once, from the work type on their active LM005S1 row to an at-risk destination.

Evidence and equations
----------------------
* Familiarity: at least one TI102C line completed by the operator in the
  destination work type by the snapshot.
* Operator pace in a work type = their completed TI102C lines by the snapshot /
  their LM005S1 hours in that work type at the snapshot.
* Destination rate after = destination team rate before + the operator's
  observed destination pace.
* Source rate after = source team rate before - the operator's observed source
  pace (unless the source has no remaining priority work).
* ETC = priority lines ahead / team rate / 24 (Excel days).

A move is allowed only when every open source row remains ready by its own pick
cutoff after removal.  This makes "sufficient slack" a calculation rather than
an arbitrary threshold.  Candidates without measurable destination productivity
are rejected; the engine never invents familiarity or speed.
"""
from __future__ import annotations

from collections import Counter

import pandas as pd

from .truck import EPS, ge, le


def _status(meets: str) -> str:
    return {"YES": "ON TIME", "NO": "AT RISK", "NO - CUTOFF PASSED": "CUTOFF PASSED",
            "UNKNOWN - NO RATE": "NO RATE", "": ""}.get(meets, meets)


def _meets(row, rate: float | None) -> tuple[object, object, str]:
    get = row.get if isinstance(row, pd.Series) else lambda name: getattr(row, name)
    open_lines, t, cutoff = get("OPEN_LINES"), get("T"), get("PICK_CUTOFF")
    if open_lines == 0:
        return "", "", ""
    if ge(t, cutoff):
        return None, None, "NO - CUTOFF PASSED"
    if rate is None or rate <= 0:
        return None, None, "UNKNOWN - NO RATE"
    etc = get("LINES_AHEAD") / rate / 24
    ready = t + etc
    return etc, ready, "YES" if le(ready, cutoff) else "NO"


def _operator_stats(inp, snapshot_no: int, t: float) -> tuple[pd.DataFrame, dict]:
    """Active operator/source rows and actual pace evidence keyed by (operator, work type)."""
    lm = inp.lm[inp.lm.SNAPSHOT_NO == snapshot_no]
    active = lm[lm.WIPIND == "Y"].sort_values("STARTDATETIME").drop_duplicates("RESOURCENAME", keep="last")
    done = inp.tc[(inp.tc.TRANSACTIONDATE == inp.config["ANALYSIS_DATE"])
                  & (inp.tc.MOVED_TO_C_AT <= t + EPS)]
    hours = lm.groupby(["RESOURCENAME", "WORKTYPE"]).TOTALTIMEINSEC.sum() / 3600
    lines = done.groupby(["RESOURCENAME", "WORKTYPE"]).size()
    stats = {}
    for key in set(hours.index) | set(lines.index):
        h, n = float(hours.get(key, 0)), int(lines.get(key, 0))
        stats[(str(key[0]), int(key[1]))] = dict(hours=h, lines=n, pace=(n / h if h > 0 and n > 0 else None))
    return active, stats


def _source_safe(rows: pd.DataFrame, rate_after: float | None,
                 conservative_rate_after: float | None = None) -> tuple[bool, float | None]:
    """Whether every source truck stays on time at both point and conservative rates."""
    open_rows = rows[rows.OPEN_LINES > 0]
    if open_rows.empty:
        return True, None
    slacks = []
    for row in open_rows.itertuples():
        _, _, point_meets = _meets(row, rate_after)
        _, ready, conservative_meets = _meets(
            row, conservative_rate_after if conservative_rate_after is not None else rate_after)
        if point_meets != "YES" or conservative_meets != "YES":
            return False, None
        slacks.append((row.PICK_CUTOFF - ready) * 1440)
    return True, min(slacks)


def _source_impact(rows: pd.DataFrame, rate_before: float | None, rate_after: float | None) -> dict:
    """Return the least-slack source truck before/after the proposed removal."""
    impacts = []
    for row in rows[rows.OPEN_LINES > 0].itertuples():
        etc_before, ready_before, meets_before = _meets(row, rate_before)
        etc_after, ready_after, meets_after = _meets(row, rate_after)
        slack_after = ((row.PICK_CUTOFF - ready_after) * 1440
                       if isinstance(ready_after, (int, float)) else None)
        impacts.append((slack_after if slack_after is not None else -1e12, dict(
            SOURCE_SHIPMENTID=row.SHIPMENTID, SOURCE_ETC_BEFORE=etc_before,
            SOURCE_READY_BEFORE=ready_before, SOURCE_STATUS_BEFORE=_status(meets_before),
            SOURCE_ETC_AFTER=etc_after, SOURCE_READY_AFTER=ready_after,
            SOURCE_PICK_CUTOFF=float(row.PICK_CUTOFF), SOURCE_STATUS_AFTER=_status(meets_after))))
    if not impacts:
        return dict(SOURCE_SHIPMENTID="No remaining source work", SOURCE_ETC_BEFORE="", SOURCE_READY_BEFORE="",
                    SOURCE_STATUS_BEFORE="COMPLETE", SOURCE_ETC_AFTER="", SOURCE_READY_AFTER="",
                    SOURCE_PICK_CUTOFF="", SOURCE_STATUS_AFTER="COMPLETE")
    return min(impacts, key=lambda item: item[0])[1]


def _simulated_familiarity(inp) -> dict[tuple[str, int], str]:
    frame = getattr(inp, "operator_experience", pd.DataFrame())
    if frame.empty:
        return {}
    return {(str(r.RESOURCENAME), int(r.WORKTYPE)): str(r.EXPERIENCE_LEVEL) for r in frame.itertuples()}


def _no_eligible_reason(rejections: Counter, destination_name: str) -> str:
    if not rejections:
        return f"No active operator on another work type is available for {destination_name}."
    if rejections["familiarity"]:
        return (f"No eligible operator: no available source operator has actual or simulated {destination_name} "
                "familiarity plus a defensible productivity basis.")
    if rejections["destination_rate"]:
        return (f"No eligible operator: {destination_name} has no actual per-person rate for estimating a "
                "simulated-familiar operator's contribution.")
    if rejections["source_safety"]:
        return "No eligible operator: removing any qualified candidate would put their source work type at risk."
    if rejections["source_uncertainty"]:
        return ("No eligible operator: the source has insufficient conservative capacity after accounting for "
                "observed productivity variation.")
    if rejections["source_rate"]:
        return "No eligible operator: source productivity cannot be reduced safely from the observed data."
    return f"No eligible operator is available for {destination_name}."


def apply_reallocation(inp, wt: pd.DataFrame, wt_names: dict) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return updated work-type rows, destination summaries, and one row per accepted move."""
    wt = wt.copy()
    if "WT_PLANNING_MEETS" not in wt:
        from .uncertainty import add_uncertainty
        wt = add_uncertainty(inp, wt)
    wt["WT_STATUS"] = wt.WT_MEETS.map(_status)
    summaries, moves = [], []
    after_by_key = {}
    meta_by_key = {}
    simulated = _simulated_familiarity(inp)

    for snapshot_no in sorted(wt.SNAPSHOT_NO.unique()):
        snap_rows = wt[wt.SNAPSHOT_NO == snapshot_no]
        t = float(snap_rows["T"].iloc[0])
        active, stats = _operator_stats(inp, int(snapshot_no), t)
        rates = {int(w): (None if pd.isna(g.ACTUALRATELINES.iloc[0]) else float(g.ACTUALRATELINES.iloc[0]))
                 for w, g in snap_rows.groupby("WORKTYPE")}
        low_rates = {int(w): (None if pd.isna(g.RATE_LOW.iloc[0]) else float(g.RATE_LOW.iloc[0]))
                     for w, g in snap_rows.groupby("WORKTYPE")}
        high_rates = {int(w): (None if pd.isna(g.RATE_HIGH.iloc[0]) else float(g.RATE_HIGH.iloc[0]))
                      for w, g in snap_rows.groupby("WORKTYPE")}
        resources = {int(w): int(g.RESOURCES.iloc[0]) for w, g in snap_rows.groupby("WORKTYPE")}
        baseline_rates, baseline_low_rates, baseline_high_rates = rates.copy(), low_rates.copy(), high_rates.copy()
        baseline_resources = resources.copy()
        used = set()
        incoming = {int(w): [] for w in snap_rows.WORKTYPE.unique()}
        outgoing = {int(w): [] for w in snap_rows.WORKTYPE.unique()}
        reasons = {int(w): [] for w in snap_rows.WORKTYPE.unique()}
        familiarity = {int(w): [] for w in snap_rows.WORKTYPE.unique()}
        eligible_candidates = {int(w): {} for w in snap_rows.WORKTYPE.unique()}
        no_eligible = {int(w): "" for w in snap_rows.WORKTYPE.unique()}

        risk_order = []
        for dest, rows in snap_rows.groupby("WORKTYPE"):
            risk = rows[rows.WT_PLANNING_MEETS == "NO"]
            if len(risk):
                risk_order.append((float(risk.PICK_CUTOFF.min()), int(dest)))

        for _, dest in sorted(risk_order):
            dest_rows = snap_rows[snap_rows.WORKTYPE == dest]
            original_risk = dest_rows[dest_rows.WT_PLANNING_MEETS == "NO"].sort_values("PICK_CUTOFF")
            priority = original_risk.iloc[0]
            rejections = Counter()

            def destination_safe(rate, conservative_rate):
                return all(_meets(row, rate)[2] == "YES" and _meets(row, conservative_rate)[2] == "YES"
                           for row in original_risk.itertuples())

            while not destination_safe(rates[dest], low_rates[dest]):
                eligible = []
                for op in active.itertuples():
                    name, source = str(op.RESOURCENAME), int(op.WORKTYPE)
                    if name in used or source == dest:
                        continue
                    source_rows = snap_rows[snap_rows.WORKTYPE == source]
                    source_open = source_rows[source_rows.OPEN_LINES > 0]
                    src = stats.get((name, source), {})
                    if len(source_open) and not src.get("pace"):
                        rejections["source_rate"] += 1
                        continue
                    dst = stats.get((name, dest), {})
                    simulated_level = simulated.get((name, dest))
                    if dst.get("pace"):
                        dest_pace = dst["pace"]
                        familiarity_type = "ACTUAL HISTORICAL"
                        evidence = (f"Actual: {dst['lines']} completed {wt_names.get(dest, f'WT {dest}')} lines over "
                                    f"{dst['hours']:.2f} observed h = {dest_pace:.1f} lines/h")
                        pace_basis = "Actual destination completions / actual destination labor hours"
                    elif simulated_level:
                        per_person = priority.get("PER_PERSON_RATE")
                        if not isinstance(per_person, (int, float)) or pd.isna(per_person) or per_person <= 0:
                            rejections["destination_rate"] += 1
                            continue
                        if not src.get("pace"):
                            rejections["source_rate"] += 1
                            continue
                        dest_pace = min(src["pace"], float(per_person))
                        familiarity_type = "SIMULATED PLANNING ASSUMPTION"
                        evidence = (f"Simulated familiarity: {simulated_level}; not verified history. Estimated "
                                    f"destination contribution = min(actual source pace {src['pace']:.1f}, actual "
                                    f"destination per-person rate {float(per_person):.1f}) = {dest_pace:.1f} lines/h")
                        pace_basis = "Estimated: MIN(actual source pace, actual destination per-person rate)"
                    else:
                        rejections["familiarity"] += 1
                        continue
                    source_rate_after = rates[source]
                    source_low_after = low_rates[source]
                    if len(source_open):
                        source_rate_after = rates[source] - src["pace"] if rates[source] is not None else None
                        source_floor = source_open.iloc[0].get("EMPIRICAL_PACE_LOW")
                        if not isinstance(source_floor, (int, float)) or pd.isna(source_floor) or source_floor <= 0:
                            source_floor = (low_rates[source] / resources[source]
                                            if low_rates[source] and resources[source] else None)
                        remaining_people = resources[source] - 1
                        source_low_after = (min(source_rate_after, remaining_people * float(source_floor))
                                            if source_floor and source_rate_after is not None else None)
                        if source_rate_after is None or source_rate_after <= 0:
                            rejections["source_rate"] += 1
                            continue
                        if source_low_after is None or source_low_after <= 0:
                            rejections["source_uncertainty"] += 1
                            continue
                    safe, source_slack = _source_safe(source_rows, source_rate_after, source_low_after)
                    if not safe:
                        rejections["source_safety"] += 1
                        continue
                    source_lines = int(source_open.LINES_AHEAD.max()) if len(source_open) else 0
                    dest_rate_after = (rates[dest] or 0) + dest_pace
                    empirical_low = priority.get("EMPIRICAL_PACE_LOW")
                    dest_pace_low = min(dest_pace, float(empirical_low)) if isinstance(empirical_low, (int, float)) and not pd.isna(empirical_low) else dest_pace
                    dest_low_after = (low_rates[dest] or 0) + dest_pace_low
                    dest_safe = destination_safe(dest_rate_after, dest_low_after)
                    eligible_candidates[dest][name] = (f"{name} (WT {source}; {familiarity_type.lower()}; "
                                                       f"{dest_pace:.1f} l/h; source safe"
                                                       + (f" with {source_slack:.0f} min slack)" if source_slack is not None else ")"))
                    eligible.append((not dest_safe, source_lines,
                                     -(source_slack if source_slack is not None else 1e12),
                                     -dest_pace_low, name, source, source_rows, source_rate_after, source_low_after,
                                     source_slack, dest_pace, dest_pace_low, dest_low_after, src,
                                     evidence, familiarity_type, pace_basis))

                if not eligible:
                    no_eligible[dest] = _no_eligible_reason(rejections, wt_names.get(dest, f"WT {dest}"))
                    break

                (_, source_lines, _, _, name, source, selected_source_rows, source_rate_after, source_low_after,
                 source_slack, dest_pace, dest_pace_low, dest_low_after, src,
                 evidence, familiarity_type, pace_basis) = sorted(eligible)[0]
                dest_rate_before = rates[dest]
                source_rate_before = rates[source]
                dest_rate_after = (dest_rate_before or 0) + dest_pace
                etc_before, ready_before, _ = _meets(priority, dest_rate_before)
                etc_after, ready_after, meets_after = _meets(priority, dest_rate_after)
                reason = (f"WT {source} has {source_lines} priority lines remaining and remains on time after the move"
                          + (f" with {source_slack:.0f} min minimum slack" if source_slack is not None else
                             " because it has no remaining open work") + ".")
                source_impact = _source_impact(selected_source_rows, source_rate_before, source_rate_after)
                moves.append(dict(SNAPSHOT_NO=int(snapshot_no), T=t, DEST_WORKTYPE=dest,
                                  DESTINATION=wt_names.get(dest, f"WT {dest}"), OPERATOR=name,
                                  SOURCE_WORKTYPE=source, SOURCE=wt_names.get(source, f"WT {source}"),
                                  ELIGIBILITY_REASON=reason, FAMILIARITY_EVIDENCE=evidence,
                                  FAMILIARITY_TYPE=familiarity_type, DEST_PACE_BASIS=pace_basis,
                                  SOURCE_RATE_BEFORE=source_rate_before, SOURCE_OPERATOR_PACE=src.get("pace"),
                                  SOURCE_RATE_AFTER=source_rate_after, SOURCE_MIN_SLACK_AFTER=source_slack,
                                  DEST_RATE_BEFORE=dest_rate_before, OPERATOR_DEST_PACE=dest_pace,
                                  DEST_RATE_AFTER=dest_rate_after, ETC_BEFORE=etc_before,
                                  READY_BEFORE=ready_before, ETC_AFTER=etc_after, READY_AFTER=ready_after,
                                  PICK_CUTOFF=float(priority.PICK_CUTOFF),
                                  TIME_SAVED=(etc_before - etc_after if isinstance(etc_before, float) and isinstance(etc_after, float) else ""),
                                  STATUS_AFTER=_status(meets_after), **source_impact))
                used.add(name)
                rates[source], rates[dest] = source_rate_after, dest_rate_after
                low_rates[source], low_rates[dest] = source_low_after, dest_low_after
                high_rates[source] = min(high_rates[source], source_rate_after) if high_rates[source] is not None else source_rate_after
                high_rates[dest] = (high_rates[dest] or 0) + dest_pace
                resources[source] -= 1
                resources[dest] += 1
                incoming[dest].append(name)
                outgoing[source].append(name)
                reasons[dest].append(reason)
                familiarity[dest].append(evidence)

            etc_before, ready_before, _ = _meets(priority, baseline_rates[dest])
            etc_after, ready_after, meets_after = _meets(priority, rates[dest])
            _, ready_late_after, conservative_after = _meets(priority, low_rates[dest])
            required = priority.get("OPERATORS_NEEDED_CONSERVATIVE", "")
            remaining = max(0, int(required) - resources[dest]) if isinstance(required, (int, float)) and not pd.isna(required) else ""
            if conservative_after != "YES" and not no_eligible[dest]:
                no_eligible[dest] = "No further familiar operator can be moved without making a source unsafe."
            summaries.append(dict(SNAPSHOT_NO=int(snapshot_no), T=t, WORKTYPE=dest,
                                  DESCRIPTION=wt_names.get(dest, f"WT {dest}"),
                                  AT_RISK_TRUCKS=len(original_risk), SHIPMENTID=priority.SHIPMENTID,
                                  OPERATORS_BEFORE=baseline_resources[dest], RATE_BEFORE=baseline_rates[dest],
                                  ETC_BEFORE=etc_before, READY_BEFORE=ready_before,
                                  REALLOCATED_OPERATORS=", ".join(incoming[dest]),
                                  SOURCE_WORKTYPES=", ".join(f"WT {m['SOURCE_WORKTYPE']}" for m in moves
                                                             if m["SNAPSHOT_NO"] == snapshot_no and m["DEST_WORKTYPE"] == dest),
                                  OPERATORS_AFTER=resources[dest], RATE_AFTER=rates[dest], ETC_AFTER=etc_after,
                                  READY_AFTER=ready_after, PICK_CUTOFF=float(priority.PICK_CUTOFF),
                                  TIME_SAVED=(etc_before - etc_after if isinstance(etc_before, float) and isinstance(etc_after, float) else ""),
                                  STATUS_BEFORE="AT RISK", STATUS_AFTER=_status(meets_after),
                                  PLANNING_STATUS_AFTER=_status(conservative_after),
                                  ALLOCATED_OPERATORS=len(incoming[dest]), REMAINING_SHORTFALL=remaining,
                                  ELIGIBLE_CANDIDATES="; ".join(eligible_candidates[dest].values()),
                                  NO_ELIGIBLE_REASON=no_eligible[dest]))

        for row in snap_rows.itertuples():
            key = (int(snapshot_no), int(row.WORKTYPE))
            etc_after, ready_after, meets_after = _meets(row, rates[int(row.WORKTYPE)])
            etc_fast, ready_early, _ = _meets(row, high_rates[int(row.WORKTYPE)])
            etc_slow, ready_late, planning_after = _meets(row, low_rates[int(row.WORKTYPE)])
            after_by_key[(key[0], row.SHIPMENTID, key[1])] = (
                etc_after, ready_after, meets_after, etc_fast, etc_slow, ready_early, ready_late, planning_after)
            meta_by_key[key] = dict(resources_after=resources[key[1]], rate_after=rates[key[1]],
                                    rate_low_after=low_rates[key[1]], rate_high_after=high_rates[key[1]],
                                    incoming=", ".join(incoming[key[1]]), outgoing=", ".join(outgoing[key[1]]),
                                    reason=" ".join(reasons[key[1]]), familiarity="; ".join(familiarity[key[1]]),
                                    candidates="; ".join(eligible_candidates[key[1]].values()),
                                    no_eligible=no_eligible[key[1]])

    resources_after, rate_after, incoming_col, outgoing_col = [], [], [], []
    reason_col, familiarity_col, candidates_col, no_eligible_col = [], [], [], []
    etc_after_col, ready_after_col, meets_after_col, saved_col = [], [], [], []
    rate_low_after_col, rate_high_after_col = [], []
    etc_low_after_col, etc_high_after_col, ready_early_after_col, ready_late_after_col, planning_after_col = [], [], [], [], []
    for row in wt.itertuples():
        meta = meta_by_key[(int(row.SNAPSHOT_NO), int(row.WORKTYPE))]
        after = after_by_key[(int(row.SNAPSHOT_NO), row.SHIPMENTID, int(row.WORKTYPE))]
        resources_after.append(meta["resources_after"]); rate_after.append(meta["rate_after"])
        rate_low_after_col.append(meta["rate_low_after"]); rate_high_after_col.append(meta["rate_high_after"])
        incoming_col.append(meta["incoming"]); outgoing_col.append(meta["outgoing"])
        reason_col.append(meta["reason"]); familiarity_col.append(meta["familiarity"])
        candidates_col.append(meta["candidates"])
        no_eligible_col.append(meta["no_eligible"])
        etc_after_col.append(after[0]); ready_after_col.append(after[1]); meets_after_col.append(after[2])
        etc_low_after_col.append(after[3]); etc_high_after_col.append(after[4])
        ready_early_after_col.append(after[5]); ready_late_after_col.append(after[6]); planning_after_col.append(after[7])
        saved_col.append(row.WT_ETC - after[0] if isinstance(row.WT_ETC, float) and isinstance(after[0], float) else "")

    wt["RESOURCES_AFTER_REALLOCATION"] = resources_after
    wt["RATE_AFTER_REALLOCATION"] = rate_after
    wt["RATE_AFTER_LOW"] = rate_low_after_col
    wt["RATE_AFTER_HIGH"] = rate_high_after_col
    wt["REALLOCATED_IN"] = incoming_col
    wt["REALLOCATED_OUT"] = outgoing_col
    wt["REALLOCATION_REASON"] = reason_col
    wt["FAMILIARITY_EVIDENCE"] = familiarity_col
    wt["ELIGIBLE_CANDIDATES"] = candidates_col
    wt["NO_ELIGIBLE_OPERATOR"] = no_eligible_col
    wt["WT_ETC_AFTER"] = etc_after_col
    wt["WT_READY_TIME_AFTER"] = ready_after_col
    wt["WT_MEETS_AFTER"] = meets_after_col
    wt["WT_STATUS_AFTER"] = pd.Series(meets_after_col).map(_status)
    wt["WT_ETC_AFTER_LOW"] = etc_low_after_col
    wt["WT_ETC_AFTER_HIGH"] = etc_high_after_col
    wt["WT_READY_AFTER_EARLY"] = ready_early_after_col
    wt["WT_READY_AFTER_LATE"] = ready_late_after_col
    wt["WT_PLANNING_MEETS_AFTER"] = planning_after_col
    wt["WT_PLANNING_STATUS_AFTER"] = pd.Series(planning_after_col).map(_status)
    wt["WT_TIME_SAVED"] = saved_col
    return wt, pd.DataFrame(summaries), pd.DataFrame(moves)
