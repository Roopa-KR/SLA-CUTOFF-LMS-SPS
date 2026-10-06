"""Truck ETC supervisor app (Flask). Run: python app.py  ->  http://127.0.0.1:5000"""
from flask import Flask, render_template, request

import config
from calculations.engine import run
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
        return "NULL"
    minutes = int(hours * 60 + 1e-6)
    return f"{minutes // 60}:{minutes % 60:02d}"


def create_app() -> Flask:
    app = Flask(__name__)
    app.jinja_env.filters.update(hhmm=hhmm, status_style=status_style,
                                 clock=lambda t: t.strftime("%H:%M") if t else "")
    db.init_db()
    if db.is_empty():
        db.rebuild(run(config.WORKBOOK_PATH))

    @app.cli.command("init-db")
    def init_db_command():
        """Recalculate everything from the workbook and reload the database."""
        db.rebuild(run(config.WORKBOOK_PATH))
        print("Database rebuilt from", config.WORKBOOK_PATH)

    @app.route("/")
    def dashboard():
        snaps = db.snapshots()
        snapshot_no = request.args.get("snapshot", default=config.DEFAULT_SNAPSHOT, type=int)
        current = next((s for s in snaps if s.snapshot_no == snapshot_no), snaps[0])
        trucks = db.trucks_at(current.snapshot_no)
        counts = {}
        for t in trucks:
            key = "DEPARTED" if t.status.startswith("DEPARTED") else t.status
            counts[key] = counts.get(key, 0) + 1
        active = [t for t in trucks if t.open_lines > 0 and not t.status.startswith("DEPARTED")]
        chart = {"trucks": [f"{t.shipment_id} ({t.departure:%H:%M})" for t in active],
                 "etc": [t.etc_hours for t in active],
                 "cutoff": [max(0, t.hours_to_cutoff) for t in active],
                 "status": [t.status for t in active]}
        return render_template("dashboard.html", snaps=snaps, current=current, trucks=trucks, counts=counts, chart=chart)

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
