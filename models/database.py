"""Database access: engine, session, (re)building the results tables from the calculation engine."""
from __future__ import annotations

import hashlib
import math
from contextlib import contextmanager
from functools import lru_cache
from types import SimpleNamespace

import pandas as pd

from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.orm import sessionmaker

import config
from calculations.excel_io import serial_to_datetime, to_serial

from .models import Base, ReallocationMove, Snapshot, TruckResult, WorktypeResult

engine = create_engine(config.DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    insp = inspect(engine)
    if insp.has_table("worktype_result"):
        cols = {c["name"] for c in insp.get_columns("worktype_result")}
        if ("reallocated_in" not in cols or "eligible_candidates" not in cols
                or "uncertainty_evidence" not in cols):
            Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def needs_rebuild(workbook_path: str) -> bool:
    """True when the database is empty, from an older version, or built from a different workbook."""
    if (not inspect(engine).has_table("src_config") or not inspect(engine).has_table("src_line_map")
            or not inspect(engine).has_table("src_operator_experience")
            or not inspect(engine).has_table("reallocation_move")):
        return True
    if "ASSIGNMENTID" not in {c["name"] for c in inspect(engine).get_columns("src_line_map")}:
        return True
    with session_scope() as s:
        if s.scalar(select(func.count()).select_from(TruckResult)) == 0:
            return True
    cfg = pd.read_sql_table("src_config", engine)
    stored_config = dict(zip(cfg.key, cfg.value))
    if not {"CLIENTID_FILTER", "WHID_FILTER"}.issubset(stored_config):
        return True
    stored = stored_config.get("WORKBOOK_STAMP")
    return stored != workbook_stamp(workbook_path)


# ---------- value conversion (engine uses Excel serial days, "" and None for blanks) ----------
def _num(v):
    if v is None or v == "" or (isinstance(v, float) and math.isnan(v)):
        return None
    return float(v)


def _time(v):
    v = _num(v)
    return serial_to_datetime(v) if v is not None else None


def _hours(days):
    v = _num(days)
    return v * 24 if v is not None else None


def _text(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return str(v)


def rebuild(results) -> None:
    """Replace all stored results with a fresh engine run."""
    names = results.worktype_names
    with session_scope() as s:
        for model in (ReallocationMove, WorktypeResult, TruckResult, Snapshot):
            s.query(model).delete()
        s.add_all(Snapshot(snapshot_no=int(r.SNAPSHOT_NO), snapshot_time=serial_to_datetime(r.T))
                  for r in results.snapshots.itertuples())
        s.add_all(TruckResult(
            snapshot_no=int(r["SNAPSHOT_NO"]), snapshot_time=serial_to_datetime(r["T"]), shipment_id=r["SHIPMENTID"],
            route_id=r["ROUTEID"], carrier=r["CARRIER"], dock_door=r["DOCK_DOOR"], departure=_time(r["DEPARTURE"]),
            pick_cutoff=_time(r["PICK_CUTOFF"]), hours_to_cutoff=r["HOURS_TO_PICK_CUTOFF"], lines_picked=int(r["LINES_PICKED"]),
            open_lines=int(r["OPEN_LINES"]), lines_released=int(r["LINES_RELEASED"]), pct_picked=_num(r["PCT_PICKED"]),
            etc_hours=_hours(r["TRUCK_ETC"]), bottleneck=_text(r["BOTTLENECK"]), ready_time=_time(r["READY_TIME"]),
            status=r["TRUCK_STATUS"], meets_departure=_text(r["MEETS_DEPARTURE"]),
            slack_minutes=None if r["SLACK_MINUTES"] == "" else int(r["SLACK_MINUTES"]), slack_text=_text(r["SLACK"]),
            operator_gap=_text(r["ADDITIONAL_OPERATORS_ON_BOTTLENECK"]), etc_after_hours=_hours(r["TRUCK_ETC_AFTER"]),
            ready_time_after=_time(r["READY_TIME_AFTER"]), status_after=_text(r["TRUCK_STATUS_AFTER"]),
            slack_after_text=_text(r["SLACK_AFTER"]),
            time_saved_minutes=None if _num(r["TIME_SAVED"]) is None else _num(r["TIME_SAVED"]) * 1440,
            reallocations=_text(r["REALLOCATIONS_ON_ITS_WORK_TYPES"]), scenario=_text(r["OB_SCENARIO_ID"]), note=_text(r["NOTE"]),
            etc_low_hours=_hours(r["TRUCK_ETC_LOW"]), etc_high_hours=_hours(r["TRUCK_ETC_HIGH"]),
            ready_time_early=_time(r["READY_TIME_EARLY"]), ready_time_late=_time(r["READY_TIME_LATE"]),
            planning_status=_text(r["PLANNING_STATUS"]), uncertainty_changes_risk=bool(r["UNCERTAINTY_CHANGES_RISK"]),
            planning_operator_gap=_text(r["PLANNING_OPERATOR_GAP"]),
            planning_slack_text=_text(r["PLANNING_SLACK"]),
            etc_after_high_hours=_hours(r["TRUCK_ETC_AFTER_HIGH"]),
            ready_time_after_late=_time(r["READY_TIME_AFTER_LATE"]),
            planning_status_after=_text(r["PLANNING_STATUS_AFTER"]),
            planning_slack_after_text=_text(r["PLANNING_SLACK_AFTER"]))
            for r in results.trucks.to_dict("records"))
        s.add_all(WorktypeResult(
            snapshot_no=int(r["SNAPSHOT_NO"]), shipment_id=r["SHIPMENTID"], worktype=int(r["WORKTYPE"]),
            worktype_name=names.get(int(r["WORKTYPE"])), open_lines=int(r["OPEN_LINES"]), lines_ahead=int(r["LINES_AHEAD"]),
            resources=int(r["RESOURCES"]), rate=_num(r["ACTUALRATELINES"]), per_person_rate=_num(r["PER_PERSON_RATE"]),
            operators_needed=_text(r["OPERATORS_NEEDED"]), wt_etc_hours=_hours(r["WT_ETC"]), status=_text(r["WT_STATUS"]),
            ready_time=_time(r["WT_READY_TIME"]), meets=_text(r["WT_MEETS"]), additional_operators=_text(r["ADDITIONAL_OPERATORS"]),
            resources_after=int(r["RESOURCES_AFTER_REALLOCATION"]), rate_after=_num(r["RATE_AFTER_REALLOCATION"]),
            reallocated_in=_text(r["REALLOCATED_IN"]), reallocated_out=_text(r["REALLOCATED_OUT"]),
            reallocation_reason=_text(r["REALLOCATION_REASON"]), familiarity_evidence=_text(r["FAMILIARITY_EVIDENCE"]),
            eligible_candidates=_text(r["ELIGIBLE_CANDIDATES"]),
            no_eligible_operator=_text(r["NO_ELIGIBLE_OPERATOR"]),
            wt_etc_after_hours=_hours(r["WT_ETC_AFTER"]), ready_time_after=_time(r["WT_READY_TIME_AFTER"]),
            meets_after=_text(r["WT_MEETS_AFTER"]), status_after=_text(r["WT_STATUS_AFTER"]),
            rate_low=_num(r["RATE_LOW"]), rate_high=_num(r["RATE_HIGH"]), observed_pace_count=int(r["OBSERVED_PACE_COUNT"]),
            uncertainty_evidence=_text(r["UNCERTAINTY_EVIDENCE"]), wt_etc_low_hours=_hours(r["WT_ETC_LOW"]),
            wt_etc_high_hours=_hours(r["WT_ETC_HIGH"]), ready_time_early=_time(r["WT_READY_EARLY"]),
            ready_time_late=_time(r["WT_READY_LATE"]), planning_meets=_text(r["WT_PLANNING_MEETS"]),
            planning_status=_text(r["WT_PLANNING_STATUS"]),
            operators_needed_conservative=_text(r["OPERATORS_NEEDED_CONSERVATIVE"]),
            additional_operators_conservative=_text(r["ADDITIONAL_OPERATORS_CONSERVATIVE"]),
            rate_after_low=_num(r["RATE_AFTER_LOW"]), rate_after_high=_num(r["RATE_AFTER_HIGH"]),
            wt_etc_after_low_hours=_hours(r["WT_ETC_AFTER_LOW"]), wt_etc_after_high_hours=_hours(r["WT_ETC_AFTER_HIGH"]),
            ready_time_after_early=_time(r["WT_READY_AFTER_EARLY"]), ready_time_after_late=_time(r["WT_READY_AFTER_LATE"]),
            planning_meets_after=_text(r["WT_PLANNING_MEETS_AFTER"]),
            planning_status_after=_text(r["WT_PLANNING_STATUS_AFTER"]))
            for r in results.worktypes.to_dict("records"))
        s.add_all(ReallocationMove(
            snapshot_no=int(r["SNAPSHOT_NO"]), destination_worktype=int(r["DEST_WORKTYPE"]),
            destination=r["DESTINATION"], operator=r["OPERATOR"], source_worktype=int(r["SOURCE_WORKTYPE"]),
            source=r["SOURCE"], eligibility_reason=r["ELIGIBILITY_REASON"],
            familiarity_evidence=r["FAMILIARITY_EVIDENCE"], familiarity_type=r["FAMILIARITY_TYPE"],
            destination_pace_basis=r["DEST_PACE_BASIS"], source_rate_before=_num(r["SOURCE_RATE_BEFORE"]),
            source_operator_pace=_num(r["SOURCE_OPERATOR_PACE"]), source_rate_after=_num(r["SOURCE_RATE_AFTER"]),
            source_min_slack_after=_num(r["SOURCE_MIN_SLACK_AFTER"]), source_shipment_id=_text(r["SOURCE_SHIPMENTID"]),
            source_etc_before_hours=_hours(r["SOURCE_ETC_BEFORE"]), source_ready_before=_time(r["SOURCE_READY_BEFORE"]),
            source_status_before=_text(r["SOURCE_STATUS_BEFORE"]), source_etc_after_hours=_hours(r["SOURCE_ETC_AFTER"]),
            source_ready_after=_time(r["SOURCE_READY_AFTER"]), source_pick_cutoff=_time(r["SOURCE_PICK_CUTOFF"]),
            source_status_after=_text(r["SOURCE_STATUS_AFTER"]), destination_rate_before=_num(r["DEST_RATE_BEFORE"]),
            operator_destination_pace=_num(r["OPERATOR_DEST_PACE"]), destination_rate_after=_num(r["DEST_RATE_AFTER"]),
            destination_etc_before_hours=_hours(r["ETC_BEFORE"]), destination_ready_before=_time(r["READY_BEFORE"]),
            destination_etc_after_hours=_hours(r["ETC_AFTER"]), destination_ready_after=_time(r["READY_AFTER"]),
            destination_pick_cutoff=_time(r["PICK_CUTOFF"]),
            time_saved_minutes=None if _num(r["TIME_SAVED"]) is None else _num(r["TIME_SAVED"]) * 1440,
            destination_status_after=_text(r["STATUS_AFTER"])) for r in results.moves.to_dict("records"))


def snapshots():
    with session_scope() as s:
        return s.scalars(select(Snapshot).order_by(Snapshot.snapshot_no)).all()


def trucks_at(snapshot_no: int):
    with session_scope() as s:
        return s.scalars(select(TruckResult).where(TruckResult.snapshot_no == snapshot_no)
                         .order_by(TruckResult.departure, TruckResult.shipment_id)).all()


# ---------- source tables for the drill-down (stored once per rebuild, read back with pandas) ----------
SOURCES = {
    "src_lm005s1": ("lm", ["SNAPSHOT_NO", "WORKTYPE", "RESOURCENAME", "STARTDATETIME", "TOTALTIMEINSEC", "WIPIND"]),
    "src_ti102": ("ti", ["SNAPSHOT_NO", "TI102ID", "WORKTYPE", "RESOURCENAME", "CURRENTSTATUSID", "OPENQTY", "CHANGEDATETIME"]),
    "src_ti102c": ("tc", ["TI102ID", "WORKTYPE", "RESOURCENAME", "PROCESSEDQTY", "SHORTQTY", "MOVED_TO_C_AT", "SRCLOC",
                          "TRANSACTIONDATE"]),
    "src_order": ("orders", ["WHORDERID", "SHIPMENTID", "ORIGINAL_SHIPMENTID", "MOVED_AT", "STOPID", "RELEASE_TIME",
                             "OB_SCENARIO_ID", "CUSTOMER", "CUSTOMER_NAME"]),
    "src_line_map": ("line_map", ["TI102ID", "WHORDERID", "WORKTYPE", "ASSIGNMENTID", "CUSTOMER"]),
    "src_operator_experience": ("operator_experience", ["RESOURCENAME", "WORKTYPE", "EXPERIENCE_LEVEL",
                                                           "EVIDENCE_TYPE", "ASSUMPTION_NOTES"]),
}


def workbook_stamp(path: str) -> str:
    """Content hash of the workbook used to build the database."""
    digest = hashlib.sha256()
    with open(path, "rb") as workbook:
        for chunk in iter(lambda: workbook.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def store_sources(inputs, stamp: str = "") -> None:
    """Keep the source rows the drill-down needs (times as Excel serial days)."""
    for table, (attr, cols) in SOURCES.items():
        frame = getattr(inputs, attr)
        frame[[c for c in cols if c in frame.columns]].to_sql(table, engine, if_exists="replace", index=False)
    pd.DataFrame([("ANALYSIS_DATE", inputs.config["ANALYSIS_DATE"]),
                  ("CLIENTID_FILTER", inputs.config["CLIENTID_FILTER"]),
                  ("WHID_FILTER", inputs.config["WHID_FILTER"]),
                  ("WORKBOOK_STAMP", stamp)],
                 columns=["key", "value"]).to_sql("src_config", engine, if_exists="replace", index=False)
    inputs.snapshots.to_sql("src_snapshot", engine, if_exists="replace", index=False)
    pd.DataFrame(list(inputs.worktype_names.items()), columns=["WORKTYPE", "NAME"]).to_sql(
        "src_worktype", engine, if_exists="replace", index=False)
    load_sources.cache_clear()


@lru_cache(maxsize=1)
def load_sources() -> SimpleNamespace:
    frames = {attr: pd.read_sql_table(table, engine) for table, (attr, _) in SOURCES.items()}
    cfg = pd.read_sql_table("src_config", engine)
    numeric = {"ANALYSIS_DATE", "WHID_FILTER"}
    frames["config"] = {k: (float(v) if k in numeric else v) for k, v in zip(cfg.key, cfg.value)}
    frames["snapshots"] = pd.read_sql_table("src_snapshot", engine)
    names = pd.read_sql_table("src_worktype", engine)
    frames["worktype_names"] = dict(zip(names.WORKTYPE.astype(int), names.NAME))
    return SimpleNamespace(**frames)


# ---------- engine-shaped rows for the drill-down (serial times, engine column names) ----------
def _serial(t):
    return to_serial(t) if t is not None else None


def _int_or_text(v):
    return int(v) if v is not None and str(v).isdigit() else (v if v is not None else "")


def truck_history(shipment_id: str) -> list[dict]:
    with session_scope() as s:
        rows = s.scalars(select(TruckResult).where(TruckResult.shipment_id == shipment_id)
                         .order_by(TruckResult.snapshot_no)).all()
    return [dict(SNAPSHOT_NO=r.snapshot_no, SHIPMENTID=r.shipment_id, TRUCK_STATUS=r.status, DEPARTURE=_serial(r.departure),
                 PICK_CUTOFF=_serial(r.pick_cutoff), READY_TIME=_serial(r.ready_time) if r.ready_time else "",
                 OPEN_LINES=r.open_lines, LINES_PICKED=r.lines_picked, LINES_RELEASED=r.lines_released,
                 SLACK_MINUTES=r.slack_minutes if r.slack_minutes is not None else "", SLACK=r.slack_text or "",
                 BOTTLENECK=r.bottleneck or "", TRUCK_STATUS_AFTER=r.status_after, SLACK_AFTER=r.slack_after_text or "",
                 ETC_HOURS=r.etc_hours, ROUTEID=r.route_id, CARRIER=r.carrier, DOCK_DOOR=r.dock_door,
                 OPERATOR_GAP=r.operator_gap or "", READY_TIME_AFTER=_serial(r.ready_time_after) if r.ready_time_after else "",
                 TIME_SAVED_MIN=r.time_saved_minutes, REALLOCATIONS=r.reallocations or "", NOTE=r.note, SCENARIO=r.scenario,
                 ETC_LOW_HOURS=r.etc_low_hours, ETC_HIGH_HOURS=r.etc_high_hours,
                 READY_TIME_EARLY=_serial(r.ready_time_early) if r.ready_time_early else "",
                 READY_TIME_LATE=_serial(r.ready_time_late) if r.ready_time_late else "",
                 PLANNING_STATUS=r.planning_status or r.status, UNCERTAINTY_CHANGES_RISK=r.uncertainty_changes_risk,
                 PLANNING_OPERATOR_GAP=r.planning_operator_gap or "", PLANNING_SLACK=r.planning_slack_text or "",
                 ETC_AFTER_HIGH_HOURS=r.etc_after_high_hours,
                 READY_TIME_AFTER_LATE=_serial(r.ready_time_after_late) if r.ready_time_after_late else "",
                 PLANNING_STATUS_AFTER=r.planning_status_after or r.status_after,
                 PLANNING_SLACK_AFTER=r.planning_slack_after_text or "")
            for r in rows]


def worktype_rows(snapshot_no: int, shipment_id: str) -> list[dict]:
    with session_scope() as s:
        rows = s.scalars(select(WorktypeResult).where(WorktypeResult.snapshot_no == snapshot_no,
                                                      WorktypeResult.shipment_id == shipment_id)
                         .order_by(WorktypeResult.worktype)).all()
    return [dict(WORKTYPE=r.worktype, OPEN_LINES=r.open_lines, LINES_AHEAD=r.lines_ahead, RESOURCES=r.resources,
                 ACTUALRATELINES=r.rate, PER_PERSON_RATE=r.per_person_rate,
                 WT_ETC=(r.wt_etc_hours / 24 if r.wt_etc_hours is not None else (None if r.open_lines else "")),
                 WT_READY_TIME=_serial(r.ready_time) if r.ready_time else "", WT_MEETS=r.meets or "",
                 OPERATORS_NEEDED=_int_or_text(r.operators_needed), ADDITIONAL_OPERATORS=_int_or_text(r.additional_operators),
                 WT_STATUS=r.status or "", RESOURCES_AFTER_REALLOCATION=r.resources_after,
                 RATE_AFTER_REALLOCATION=r.rate_after, REALLOCATED_IN=r.reallocated_in or "",
                 REALLOCATED_OUT=r.reallocated_out or "", REALLOCATION_REASON=r.reallocation_reason or "",
                 FAMILIARITY_EVIDENCE=r.familiarity_evidence or "", NO_ELIGIBLE_OPERATOR=r.no_eligible_operator or "",
                 ELIGIBLE_CANDIDATES=r.eligible_candidates or "",
                 WT_READY_TIME_AFTER=_serial(r.ready_time_after) if r.ready_time_after else "", WT_MEETS_AFTER=r.meets_after or "",
                 WT_STATUS_AFTER=r.status_after or "", WT_ETC_AFTER=(r.wt_etc_after_hours / 24 if r.wt_etc_after_hours is not None else None),
                 RATE_LOW=r.rate_low, RATE_HIGH=r.rate_high, OBSERVED_PACE_COUNT=r.observed_pace_count,
                 UNCERTAINTY_EVIDENCE=r.uncertainty_evidence or "",
                 WT_ETC_LOW=(r.wt_etc_low_hours / 24 if r.wt_etc_low_hours is not None else None),
                 WT_ETC_HIGH=(r.wt_etc_high_hours / 24 if r.wt_etc_high_hours is not None else None),
                 WT_READY_EARLY=_serial(r.ready_time_early) if r.ready_time_early else "",
                 WT_READY_LATE=_serial(r.ready_time_late) if r.ready_time_late else "",
                 WT_PLANNING_MEETS=r.planning_meets or "", WT_PLANNING_STATUS=r.planning_status or "",
                 OPERATORS_NEEDED_CONSERVATIVE=_int_or_text(r.operators_needed_conservative),
                 ADDITIONAL_OPERATORS_CONSERVATIVE=_int_or_text(r.additional_operators_conservative),
                 RATE_AFTER_LOW=r.rate_after_low, RATE_AFTER_HIGH=r.rate_after_high,
                 WT_ETC_AFTER_LOW=(r.wt_etc_after_low_hours / 24 if r.wt_etc_after_low_hours is not None else None),
                 WT_ETC_AFTER_HIGH=(r.wt_etc_after_high_hours / 24 if r.wt_etc_after_high_hours is not None else None),
                 WT_READY_AFTER_EARLY=_serial(r.ready_time_after_early) if r.ready_time_after_early else "",
                 WT_READY_AFTER_LATE=_serial(r.ready_time_after_late) if r.ready_time_after_late else "",
                 WT_PLANNING_MEETS_AFTER=r.planning_meets_after or "",
                 WT_PLANNING_STATUS_AFTER=r.planning_status_after or "")
            for r in rows]


def worktype_context(snapshot_no: int) -> list[dict]:
    """All work-type rows at a snapshot with the truck timing needed for safety checks."""
    with session_scope() as s:
        rows = s.execute(select(WorktypeResult, TruckResult).join(
            TruckResult, (TruckResult.snapshot_no == WorktypeResult.snapshot_no)
            & (TruckResult.shipment_id == WorktypeResult.shipment_id)).where(
                WorktypeResult.snapshot_no == snapshot_no)).all()
    return [dict(SNAPSHOT_NO=w.snapshot_no, T=_serial(t.snapshot_time), SHIPMENTID=w.shipment_id,
                 WORKTYPE=w.worktype, OPEN_LINES=w.open_lines, LINES_AHEAD=w.lines_ahead,
                 RESOURCES=w.resources, ACTUALRATELINES=w.rate, PER_PERSON_RATE=w.per_person_rate,
                 WT_ETC=(w.wt_etc_hours / 24 if w.wt_etc_hours is not None else None),
                 WT_READY_TIME=_serial(w.ready_time), WT_MEETS=w.meets or "", WT_STATUS=w.status or "",
                 PICK_CUTOFF=_serial(t.pick_cutoff), RATE_LOW=w.rate_low,
                 WT_PLANNING_MEETS=w.planning_meets or "") for w, t in rows]


def reallocation_moves(snapshot_no: int, destination_worktype: int | None = None) -> list[dict]:
    with session_scope() as s:
        query = select(ReallocationMove).where(ReallocationMove.snapshot_no == snapshot_no)
        if destination_worktype is not None:
            query = query.where(ReallocationMove.destination_worktype == destination_worktype)
        rows = s.scalars(query.order_by(ReallocationMove.id)).all()
    return [dict(operator=r.operator, destination_worktype=r.destination_worktype,
                 destination=r.destination, source_worktype=r.source_worktype, source=r.source,
                 eligibility=r.eligibility_reason, familiarity=r.familiarity_evidence,
                 familiarity_type=r.familiarity_type, destination_pace_basis=r.destination_pace_basis,
                 source_rate_before=r.source_rate_before, source_operator_pace=r.source_operator_pace,
                 source_rate_after=r.source_rate_after, source_min_slack_after=r.source_min_slack_after,
                 source_shipment_id=r.source_shipment_id,
                 source_etc_before=(r.source_etc_before_hours / 24 if r.source_etc_before_hours is not None else None),
                 source_ready_before=_serial(r.source_ready_before), source_status_before=r.source_status_before,
                 source_etc_after=(r.source_etc_after_hours / 24 if r.source_etc_after_hours is not None else None),
                 source_ready_after=_serial(r.source_ready_after),
                 source_cutoff=_serial(r.source_pick_cutoff), source_status_after=r.source_status_after,
                 destination_rate_before=r.destination_rate_before,
                 operator_destination_pace=r.operator_destination_pace,
                 destination_rate_after=r.destination_rate_after,
                 destination_etc_before=(r.destination_etc_before_hours / 24 if r.destination_etc_before_hours is not None else None),
                 destination_ready_before=_serial(r.destination_ready_before),
                 destination_etc_after=(r.destination_etc_after_hours / 24 if r.destination_etc_after_hours is not None else None),
                 destination_ready_after=_serial(r.destination_ready_after),
                 destination_cutoff=_serial(r.destination_pick_cutoff), time_saved_minutes=r.time_saved_minutes,
                 destination_status_after=r.destination_status_after) for r in rows]
