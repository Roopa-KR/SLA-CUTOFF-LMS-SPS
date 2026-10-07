"""Database access: engine, session, (re)building the results tables from the calculation engine."""
from __future__ import annotations

import math
from contextlib import contextmanager
from functools import lru_cache
from types import SimpleNamespace

import pandas as pd

from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.orm import sessionmaker

import config
from calculations.excel_io import serial_to_datetime, to_serial

from .models import Base, Snapshot, TruckResult, WorktypeResult

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
        if "per_person_rate" not in cols:      # database from an older version: start again
            Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def is_empty() -> bool:
    if not inspect(engine).has_table("src_ti102"):
        return True
    with session_scope() as s:
        return s.scalar(select(func.count()).select_from(TruckResult)) == 0


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
        for model in (WorktypeResult, TruckResult, Snapshot):
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
            temps_added=_text(r["TEMPS_ADDED_ON_ITS_WORK_TYPES"]), scenario=_text(r["OB_SCENARIO_ID"]), note=_text(r["NOTE"]))
            for r in results.trucks.to_dict("records"))
        s.add_all(WorktypeResult(
            snapshot_no=int(r["SNAPSHOT_NO"]), shipment_id=r["SHIPMENTID"], worktype=int(r["WORKTYPE"]),
            worktype_name=names.get(int(r["WORKTYPE"])), open_lines=int(r["OPEN_LINES"]), lines_ahead=int(r["LINES_AHEAD"]),
            resources=int(r["RESOURCES"]), rate=_num(r["ACTUALRATELINES"]), per_person_rate=_num(r["PER_PERSON_RATE"]),
            operators_needed=_text(r["OPERATORS_NEEDED"]), wt_etc_hours=_hours(r["WT_ETC"]),
            ready_time=_time(r["WT_READY_TIME"]), meets=_text(r["WT_MEETS"]), additional_operators=_text(r["ADDITIONAL_OPERATORS"]),
            temps_added=int(r["TEMPS_ADDED"]), temp_names=_text(r["TEMP_OPERATORS_ADDED"]),
            wt_etc_after_hours=_hours(r["WT_ETC_AFTER"]), ready_time_after=_time(r["WT_READY_TIME_AFTER"]),
            meets_after=_text(r["WT_MEETS_AFTER"]))
            for r in results.worktypes.to_dict("records"))


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
                             "OB_SCENARIO_ID"]),
    "src_line_map": ("line_map", ["TI102ID", "WHORDERID", "WORKTYPE"]),
}


def store_sources(inputs) -> None:
    """Keep the source rows the drill-down needs (times as Excel serial days)."""
    for table, (attr, cols) in SOURCES.items():
        getattr(inputs, attr)[cols].to_sql(table, engine, if_exists="replace", index=False)
    pd.DataFrame([("ANALYSIS_DATE", inputs.config["ANALYSIS_DATE"])], columns=["key", "value"]).to_sql(
        "src_config", engine, if_exists="replace", index=False)
    inputs.snapshots.to_sql("src_snapshot", engine, if_exists="replace", index=False)
    pd.DataFrame(list(inputs.worktype_names.items()), columns=["WORKTYPE", "NAME"]).to_sql(
        "src_worktype", engine, if_exists="replace", index=False)
    load_sources.cache_clear()


@lru_cache(maxsize=1)
def load_sources() -> SimpleNamespace:
    frames = {attr: pd.read_sql_table(table, engine) for table, (attr, _) in SOURCES.items()}
    cfg = pd.read_sql_table("src_config", engine)
    frames["config"] = dict(zip(cfg.key, cfg.value))
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
                 TIME_SAVED_MIN=r.time_saved_minutes, TEMPS_ADDED=r.temps_added or "", NOTE=r.note, SCENARIO=r.scenario)
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
                 TEMPS_ADDED=r.temps_added, TEMP_OPERATORS_ADDED=r.temp_names or "",
                 WT_READY_TIME_AFTER=_serial(r.ready_time_after) if r.ready_time_after else "", WT_MEETS_AFTER=r.meets_after or "")
            for r in rows]
