"""Truck ETC supervisor app (Flask). Run: python app.py  ->  http://127.0.0.1:5000"""
from flask import Flask, abort, render_template, request

import config
from calculations.detail import clock as serial_clock, dur, truck_detail
from calculations.engine import run
from calculations.excel_io import serial_to_datetime
from calculations.operator import operator_profile
from models import database as db

STATUS_STYLE = {"ON TIME": "success", "ALL PICKED": "success", "AT RISK": "danger", "PICK CUTOFF PASSED - LATE": "danger",
                "NO RATE": "warning", "DEPARTED COMPLETE": "secondary", "NO WORK RELEASED YET": "light"}


def status_style(status: str | None) -> str:
    if not status:
        return "light"
    if status.startswith("DEPARTED - "):
        return "dark"
    return STATUS_STYLE.get(status, "light")


def hhmm(hours) -> str:
    if hours is None:
        return "—"
    minutes = int(hours * 60 + 1e-6)
    return f"{minutes // 60}:{minutes % 60:02d}"


def rebuild_all() -> None:
    """Workbook -> calculation engine -> SQLite (results + the source rows the drill-down needs)."""
    results = run(config.WORKBOOK_PATH)
    db.rebuild(results)
    db.store_sources(results.inputs, db.workbook_stamp(config.WORKBOOK_PATH))


def create_app() -> Flask:
    app = Flask(__name__)
    app.jinja_env.filters.update(hhmm=hhmm, status_style=status_style,
                                 clock=lambda t: t.strftime("%H:%M") if t else "")
    app.jinja_env.filters.update(sclock=serial_clock, dur=dur,
                                 pct=lambda v: f"{v:.0%}" if isinstance(v, (int, float)) else "",
                                 num=lambda v, d=0: f"{v:,.{d}f}" if isinstance(v, (int, float)) else "")
    db.init_db()
    if db.needs_rebuild(config.WORKBOOK_PATH):
        rebuild_all()

    @app.context_processor
    def workbook_identity():
        src = db.load_sources()
        analysis_date = serial_to_datetime(float(src.config["ANALYSIS_DATE"]))
        return dict(business_client=src.config["CLIENTID_FILTER"],
                    warehouse_id=int(float(src.config["WHID_FILTER"])),
                    analysis_date=analysis_date.strftime("%d-%b-%Y"))

    @app.cli.command("init-db")
    def init_db_command():
        """Recalculate everything from the workbook and reload the database."""
        rebuild_all()
        print("Database rebuilt from", config.WORKBOOK_PATH)

    @app.route("/")
    def dashboard():
        snaps = db.snapshots()
        snapshot_no = request.args.get("snapshot", default=config.DEFAULT_SNAPSHOT, type=int)
        current = next((s for s in snaps if s.snapshot_no == snapshot_no), snaps[0])
        trucks = db.trucks_at(current.snapshot_no)
        counts = {}
        for t in trucks:
            shown_status = t.planning_status or t.status
            key = "DEPARTED" if shown_status.startswith("DEPARTED") else shown_status
            counts[key] = counts.get(key, 0) + 1
        active = [t for t in trucks if t.open_lines > 0 and not t.status.startswith("DEPARTED")]
        chart = {"trucks": [f"{t.shipment_id} ({t.departure:%H:%M})" for t in active],
                 "etc": [t.etc_hours for t in active],
                 "etc_high": [t.etc_high_hours for t in active],
                 "cutoff": [max(0, t.hours_to_cutoff) for t in active],
                 "status": [t.planning_status or t.status for t in active]}
        return render_template("dashboard.html", snaps=snaps, current=current, trucks=trucks, counts=counts, chart=chart)

    @app.route("/truck/<shipment_id>")
    def truck(shipment_id):
        snaps = db.snapshots()
        snapshot_no = request.args.get("snapshot", default=config.DEFAULT_SNAPSHOT, type=int)
        current = next((s for s in snaps if s.snapshot_no == snapshot_no), snaps[0])
        hist = db.truck_history(shipment_id)
        if not hist:
            abort(404)
        src = db.load_sources()
        detail = truck_detail(src, src.snapshots, current.snapshot_no, shipment_id, hist,
                              db.worktype_rows(current.snapshot_no, shipment_id), src.worktype_names,
                              db.reallocation_moves(current.snapshot_no))
        iso = lambda v: serial_to_datetime(v).isoformat(timespec="seconds") if isinstance(v, (int, float)) else None
        chart = dict(now=iso(detail["t"]), cutoff=iso(detail["truck"]["PICK_CUTOFF"]), trends=detail["trends"],
                     wt_names={str(k): v for k, v in src.worktype_names.items()},
                     worktypes=[dict(name=f'{w["worktype"]} {w["name"]}', ready=iso(w["ready"]), meets=w["meets"],
                                     ready_late=iso(w["ready_late"]), planning_meets=w["planning_meets"],
                                     ready_after=iso(w["ready_after"]), ready_after_late=iso(w["ready_after_late"]),
                                     reallocated=bool(w["reallocated_in"]))
                                for w in detail["worktypes"]],
                     operators=[dict(name=o["name"], status=o["status"], pace=o["pace"], wt_avg=o["wt_avg"],
                                     pct_of_avg=o["pct_of_avg"]) for o in detail["operators"]])
        return render_template("truck_detail.html", snaps=snaps, current=current, d=detail, ship=shipment_id, chart=chart,
                               wt_names=src.worktype_names,
                               style=status_style(detail["truck"].get("PLANNING_STATUS") or detail["truck"]["TRUCK_STATUS"]))

    @app.route("/operator/<operator_name>")
    def operator(operator_name):
        snaps = db.snapshots()
        snapshot_no = request.args.get("snapshot", default=config.DEFAULT_SNAPSHOT, type=int)
        current = next((s for s in snaps if s.snapshot_no == snapshot_no), snaps[0])
        src = db.load_sources()
        profile = operator_profile(src, src.snapshots, current.snapshot_no, operator_name,
                                   db.worktype_context(current.snapshot_no), src.worktype_names,
                                   [m for m in db.reallocation_moves(current.snapshot_no)
                                    if m["operator"] == operator_name])
        if profile is None:
            abort(404)
        return render_template("operator_detail.html", snaps=snaps, current=current, o=profile,
                               wt_names=src.worktype_names)

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
