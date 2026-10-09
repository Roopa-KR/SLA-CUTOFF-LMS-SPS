"""Operator profile calculations with strict actual/simulated provenance."""
from __future__ import annotations

import pandas as pd

from .reallocation import _source_safe
from .truck import EPS


def operator_profile(src, snapshots: pd.DataFrame, snapshot_no: int, name: str,
                     worktype_rows: list[dict], wt_names: dict, selected_moves: list[dict]) -> dict | None:
    t = float(snapshots.loc[snapshots.SNAPSHOT_NO == snapshot_no, "T"].iloc[0])
    all_lm = src.lm[src.lm.RESOURCENAME.astype(str) == name]
    if all_lm.empty:
        return None
    lm = all_lm[all_lm.SNAPSHOT_NO == snapshot_no].sort_values("STARTDATETIME")
    if lm.empty:
        return None
    active = lm[lm.WIPIND == "Y"]
    current = (active if len(active) else lm).iloc[-1]
    current_wt = int(current.WORKTYPE)
    logged_in = bool(len(active))

    day = src.config["ANALYSIS_DATE"]
    done = src.tc[(src.tc.RESOURCENAME.astype(str) == name)
                  & (src.tc.TRANSACTIONDATE == day) & (src.tc.MOVED_TO_C_AT <= t + EPS)]
    ti = src.ti[(src.ti.SNAPSHOT_NO == snapshot_no) & (src.ti.RESOURCENAME.astype(str) == name)
                & (src.ti.OPENQTY > 0)]
    if not logged_in:
        status = "Logged out"
    elif (ti.CURRENTSTATUSID == 2).any():
        status = "Working"
    elif len(ti):
        status = "Assigned, not started"
    else:
        status = "Idle"

    hours_total = float(lm.TOTALTIMEINSEC.sum() / 3600)
    lines_total = int(len(done))
    pieces_total = float(done.PROCESSEDQTY.sum())
    actual_rate = lines_total / hours_total if lines_total and hours_total > 0 else None
    worktype_metrics = []
    actual_familiar = {}
    for wt in sorted(set(lm.WORKTYPE.astype(int)) | set(done.WORKTYPE.astype(int))):
        hours = float(lm[lm.WORKTYPE == wt].TOTALTIMEINSEC.sum() / 3600)
        picks = done[done.WORKTYPE == wt]
        lines = int(len(picks))
        pace = lines / hours if lines and hours > 0 else None
        worktype_metrics.append(dict(worktype=wt, name=wt_names.get(wt, ""), lines=lines,
                                     pieces=float(picks.PROCESSEDQTY.sum()), hours=hours, pace=pace))
        if lines:
            actual_familiar[wt] = f"{lines} actual completed lines by this snapshot"

    simulated = src.operator_experience[src.operator_experience.RESOURCENAME.astype(str) == name]
    familiarity = []
    for wt, evidence in actual_familiar.items():
        familiarity.append(dict(worktype=wt, name=wt_names.get(wt, ""), level="Observed work",
                                provenance="ACTUAL HISTORICAL", evidence=evidence))
    for row in simulated.itertuples():
        wt = int(row.WORKTYPE)
        if wt not in actual_familiar:
            familiarity.append(dict(worktype=wt, name=wt_names.get(wt, ""), level=str(row.EXPERIENCE_LEVEL),
                                    provenance="SIMULATED PLANNING ASSUMPTION",
                                    evidence="Not verified historical work or training data"))
    familiarity.sort(key=lambda x: (x["worktype"], x["provenance"]))

    assignments = []
    prior = None
    for snap in sorted(all_lm[all_lm.SNAPSHOT_NO <= snapshot_no].SNAPSHOT_NO.unique()):
        rows = all_lm[all_lm.SNAPSHOT_NO == snap].sort_values("STARTDATETIME")
        live = rows[rows.WIPIND == "Y"]
        row = (live if len(live) else rows).iloc[-1]
        wt = int(row.WORKTYPE)
        if prior != wt:
            snap_time = float(snapshots.loc[snapshots.SNAPSHOT_NO == snap, "T"].iloc[0])
            assignments.append(dict(snapshot=int(snap), time=snap_time, worktype=wt,
                                    name=wt_names.get(wt, ""), active=bool(len(live))))
            prior = wt

    current_metric = next((m for m in worktype_metrics if m["worktype"] == current_wt), None)
    current_pace = current_metric["pace"] if current_metric else None
    remaining_lines = int(len(ti))
    remaining_pieces = float(ti.OPENQTY.sum()) if len(ti) else 0
    etc = remaining_lines / current_pace / 24 if remaining_lines and current_pace else (0 if not remaining_lines else None)

    context = pd.DataFrame(worktype_rows)
    source_rows = context[context.WORKTYPE == current_wt] if len(context) else context
    source_rate = (float(source_rows.ACTUALRATELINES.iloc[0])
                   if len(source_rows) and pd.notna(source_rows.ACTUALRATELINES.iloc[0]) else None)
    source_rate_after = source_rate - current_pace if source_rate is not None and current_pace else None
    source_low = (float(source_rows.RATE_LOW.iloc[0])
                  if len(source_rows) and "RATE_LOW" in source_rows and pd.notna(source_rows.RATE_LOW.iloc[0]) else None)
    source_resources = int(source_rows.RESOURCES.iloc[0]) if len(source_rows) else 0
    source_floor = source_low / source_resources if source_low and source_resources else None
    source_low_after = (min(source_rate_after, (source_resources - 1) * source_floor)
                        if source_rate_after is not None and source_floor and source_resources > 1 else None)
    source_safe, source_slack = (_source_safe(source_rows, source_rate_after, source_low_after)
                                 if len(source_rows) else (False, None))
    assigned_open = source_rows[source_rows.OPEN_LINES > 0] if len(source_rows) else source_rows
    if len(assigned_open):
        cutoff_row = assigned_open.sort_values("PICK_CUTOFF").iloc[0]
        cutoff = float(cutoff_row.PICK_CUTOFF)
        cutoff_status = ("AT RISK" if (assigned_open.WT_MEETS == "NO").any()
                         else "NO RATE" if assigned_open.WT_MEETS.astype(str).str.startswith("UNKNOWN").any()
                         else "ON TIME")
    else:
        cutoff, cutoff_status = None, "No open work"

    familiar_wts = {f["worktype"]: f for f in familiarity}
    eligibility = []
    for dest, fam in sorted(familiar_wts.items()):
        if dest == current_wt:
            continue
        dest_rows = context[context.WORKTYPE == dest] if len(context) else context
        risk_column = "WT_PLANNING_MEETS" if "WT_PLANNING_MEETS" in dest_rows else "WT_MEETS"
        at_risk = dest_rows[dest_rows[risk_column] == "NO"] if len(dest_rows) else dest_rows
        per_person = (float(dest_rows.PER_PERSON_RATE.dropna().iloc[0])
                      if len(dest_rows) and len(dest_rows.PER_PERSON_RATE.dropna()) else None)
        estimated_pace = min(current_pace, per_person) if current_pace and per_person else None
        if not logged_in:
            state, reason = "Not eligible", "Operator is not currently logged in."
        elif not current_pace:
            state, reason = "Not eligible", "Actual source productivity cannot be measured."
        elif not source_safe:
            state, reason = "Not eligible", "Removing the operator would make source work miss a cutoff."
        elif not len(at_risk):
            state, reason = "Qualified; not needed", "No familiar destination work type is currently At Risk."
        elif estimated_pace is None:
            state, reason = "Not eligible", "Destination has no actual per-person rate for a defensible estimate."
        else:
            state = "Eligible candidate"
            reason = (f"Source remains on time with {source_slack:.0f} min minimum slack; estimated destination "
                      f"contribution {estimated_pace:.1f} lines/h.")
        eligibility.append(dict(worktype=dest, name=wt_names.get(dest, ""), state=state, reason=reason,
                                familiarity=fam, estimated_pace=estimated_pace,
                                at_risk_trucks=int(len(at_risk))))

    return dict(name=name, snapshot_no=snapshot_no, t=t, current_worktype=current_wt,
                current_worktype_name=wt_names.get(current_wt, ""), status=status, logged_in=logged_in,
                familiarity=familiarity, assignments=assignments, lines_total=lines_total,
                pieces_total=pieces_total, working_hours=hours_total, productive_hours=None,
                actual_rate=actual_rate, worktype_metrics=worktype_metrics, remaining_lines=remaining_lines,
                remaining_pieces=remaining_pieces, etc=etc, cutoff=cutoff, cutoff_status=cutoff_status,
                eligibility=eligibility, selected_moves=selected_moves)
