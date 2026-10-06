"""Database tables for the calculated Truck ETC results (SQLAlchemy 2.0, SQLite now, PostgreSQL-ready)."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Snapshot(Base):
    __tablename__ = "snapshot"
    snapshot_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_time: Mapped[dt.datetime] = mapped_column(DateTime)


class TruckResult(Base):
    """One row per snapshot x truck (workbook sheet TRUCK_ETC)."""
    __tablename__ = "truck_result"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_no: Mapped[int] = mapped_column(Integer, index=True)
    snapshot_time: Mapped[dt.datetime] = mapped_column(DateTime)
    shipment_id: Mapped[str] = mapped_column(String(20), index=True)
    route_id: Mapped[str | None] = mapped_column(String(20))
    carrier: Mapped[str | None] = mapped_column(String(40))
    dock_door: Mapped[str | None] = mapped_column(String(10))
    departure: Mapped[dt.datetime] = mapped_column(DateTime)
    pick_cutoff: Mapped[dt.datetime] = mapped_column(DateTime)
    hours_to_cutoff: Mapped[float] = mapped_column(Float)
    lines_picked: Mapped[int] = mapped_column(Integer)
    open_lines: Mapped[int] = mapped_column(Integer)
    lines_released: Mapped[int] = mapped_column(Integer)
    pct_picked: Mapped[float | None] = mapped_column(Float)
    etc_hours: Mapped[float | None] = mapped_column(Float)          # None = NULL (no rate)
    bottleneck: Mapped[str | None] = mapped_column(String(30))
    ready_time: Mapped[dt.datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(60))
    meets_departure: Mapped[str | None] = mapped_column(String(10))
    slack_minutes: Mapped[int | None] = mapped_column(Integer)
    slack_text: Mapped[str | None] = mapped_column(String(12))
    operator_gap: Mapped[str | None] = mapped_column(String(40))    # extra pickers on the bottleneck (or reason)
    etc_after_hours: Mapped[float | None] = mapped_column(Float)
    ready_time_after: Mapped[dt.datetime | None] = mapped_column(DateTime)
    status_after: Mapped[str | None] = mapped_column(String(60))
    slack_after_text: Mapped[str | None] = mapped_column(String(12))
    time_saved_minutes: Mapped[float | None] = mapped_column(Float)
    temps_added: Mapped[str | None] = mapped_column(String(80))
    scenario: Mapped[str | None] = mapped_column(String(40))
    note: Mapped[str | None] = mapped_column(String(300))


class WorktypeResult(Base):
    """One row per snapshot x truck x work type (workbook sheet TRUCK_WT_ETC)."""
    __tablename__ = "worktype_result"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_no: Mapped[int] = mapped_column(Integer, index=True)
    shipment_id: Mapped[str] = mapped_column(String(20), index=True)
    worktype: Mapped[int] = mapped_column(Integer)
    worktype_name: Mapped[str | None] = mapped_column(String(40))
    open_lines: Mapped[int] = mapped_column(Integer)
    lines_ahead: Mapped[int] = mapped_column(Integer)
    resources: Mapped[int] = mapped_column(Integer)
    rate: Mapped[float | None] = mapped_column(Float)
    wt_etc_hours: Mapped[float | None] = mapped_column(Float)
    ready_time: Mapped[dt.datetime | None] = mapped_column(DateTime)
    meets: Mapped[str | None] = mapped_column(String(30))
    additional_operators: Mapped[str | None] = mapped_column(String(40))
    temps_added: Mapped[int] = mapped_column(Integer)
    temp_names: Mapped[str | None] = mapped_column(String(200))
    wt_etc_after_hours: Mapped[float | None] = mapped_column(Float)
    ready_time_after: Mapped[dt.datetime | None] = mapped_column(DateTime)
    meets_after: Mapped[str | None] = mapped_column(String(30))
