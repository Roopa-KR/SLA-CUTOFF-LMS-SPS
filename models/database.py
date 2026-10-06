"""Database access: engine, session, (re)building the results tables from the calculation engine."""
from __future__ import annotations

import math
from contextlib import contextmanager

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

import config
from calculations.excel_io import serial_to_datetime

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
    Base.metadata.create_all(engine)


def is_empty() -> bool:
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
            resources=int(r["RESOURCES"]), rate=_num(r["ACTUALRATELINES"]), wt_etc_hours=_hours(r["WT_ETC"]),
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
