# Truck ETC supervisor app (SMD) - first local version

Will each truck's work be picked before its pick cutoff? This app recalculates the Truck ETC of the Excel
workbook in Python, stores the results in SQLite and shows them in a Flask dashboard.

The Excel workbook (`data/workbook.xlsx` = SMD_Truck_ETC_2026-09-17.xlsx) is the **golden reference**:
the Python engine reproduces every value of its truck sheets (see "Parity").

## Run it in VS Code

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows   (macOS / Linux: source .venv/bin/activate)
pip install -r requirements.txt
python app.py                   # first start builds data/truck_etc.db from the workbook (~5 s)
```

Open http://127.0.0.1:5000 and pick a snapshot (16:15 ... 20:30).

| Task | Command |
| --- | --- |
| Run all tests (calculations, Excel parity, dashboard) | `pytest` |
| Rebuild the database after changing the workbook | `flask --app app init-db` |
| Settings | `.env` (workbook path, database URL, default snapshot) |

Bootstrap and Plotly.js load from their CDN, so the browser needs internet access for styling and charts.

## Project layout (only what is used now)

```text
app.py                  Flask app: dashboard route + `init-db` command
config.py / .env        settings
calculations/
  excel_io.py           reads inputs and the cached Excel results (openpyxl -> pandas)
  etc.py                the ETC equation per snapshot x work type (ETC_CALC)
  truck.py              truck ETC per work type and per truck (TRUCK_WT_ETC, TRUCK_ETC)
  what_if.py            temporary operators (TRUCK_TEMP_NEED, TRUCK_TEMP_LM005S1, after-adding columns)
  engine.py             runs the whole calculation
models/
  models.py             SQLAlchemy tables: snapshot, truck_result, worktype_result
  database.py           engine / session, rebuild from the calculation, queries
templates/, static/     Jinja2 + Bootstrap 5 + Plotly.js dashboard
tests/                  pytest: test_etc.py, test_truck.py, test_excel_parity.py, test_app.py
```

SQLite is reached only through SQLAlchemy (`DATABASE_URL`), so PostgreSQL can replace it later without touching
the calculation code.

## Phase 1 - how the workbook calculates the Truck ETC

**Inputs (workbook sheets)**

| Sheet | Used for |
| --- | --- |
| CONFIG | analysis date, filters (client SMD, warehouse 200 = 0200, WIPIND Y, status 3), the 18 snapshots |
| LM005S1 | labour segments per snapshot: time, pickers logged in |
| TI102 / TI102C | open lines per snapshot / completed lines with completion time |
| OB_SHIPMENT | trucks: departure, departure change (time it was announced) |
| OB_ORDER, OB_LINE_MAP | order -> truck (with moved orders), pick line -> order |
| TRUCK_CONFIG | LOADING_MINUTES 30, TEMP_PACE_PCT 100% |
| TEMP_OPERATORS | 10 temporary operators and the work types each can work |

**Steps (all per snapshot T)**

1. ETC equation per work type (ETC_CALC): TOTALTIMEINHRS = SUM(TOTALTIMEINSEC)/3600; TOTALLINES = completed lines
   (TI102 + TI102C with completion <= T); RESOURCES = distinct pickers with WIPIND Y;
   ACTUALRATELINES = TOTALLINES / TOTALTIMEINHRS x RESOURCES; PER_PERSON_RATE = TOTALLINES / TOTALTIMEINHRS.
2. Truck deadline: departure as known at T (a changed departure applies from DEPARTURE_CHANGED_AT);
   PICK_CUTOFF = departure - LOADING_MINUTES.
3. Per truck and work type (TRUCK_WT_ETC): OPEN_LINES = open TI102 lines of the truck (order's truck as known at T);
   LINES_AHEAD = open lines of that work type on trucks with the same or earlier cutoff; WT_ETC = LINES_AHEAD /
   ACTUALRATELINES; WT_READY_TIME = T + WT_ETC; meets cutoff YES / NO / NO - CUTOFF PASSED / UNKNOWN - NO RATE;
   OPERATORS_NEEDED = ROUNDUP(LINES_AHEAD / (PER_PERSON_RATE x hours to cutoff)), ADDITIONAL = needed - RESOURCES.
4. Per truck (TRUCK_ETC): TRUCK_ETC = largest WT_ETC (BOTTLENECK = that work type; NULL if a needed work type has
   no rate); READY_TIME = T + TRUCK_ETC; SLACK = PICK_CUTOFF - READY_TIME; status (first rule that applies):
   DEPARTED (complete / n lines left behind), NO WORK RELEASED YET, ALL PICKED, PICK CUTOFF PASSED - LATE,
   NO RATE, ON TIME, AT RISK.
5. What-if, temporary operators: TEMPS_NEEDED per work type = largest ADDITIONAL among the trucks it makes late;
   each temporary operator (pool order) goes to the work type they can work that still needs people and whose
   earliest at-risk truck has the earliest cutoff; WT_ETC_AFTER = LINES_AHEAD / (PER_PERSON_RATE x (RESOURCES +
   TEMPS x TEMP_PACE_PCT)); the truck is recalculated the same way. LM005S1 is never changed.

**Edge cases handled:** no pickers on a work type (NULL rate), cutoff already passed, departed trucks (lines left
behind), departure changed during the day, order moved to another truck, no work released yet, cancelled lines,
temporary-operator pool smaller than the need (shortfall).

## Parity with Excel

`tests/test_excel_parity.py` compares every value of ETC_CALC, TRUCK_WT_ETC, TRUCK_ETC and TRUCK_TEMP_NEED
(all 18 snapshots) with the cached workbook values: all equal.

Times are kept as Excel serial numbers (days) so comparisons match Excel. Two tiny tolerances are needed and
documented in the code:
* time comparisons allow 1e-9 day, because Python's conversion of a time can differ from Excel's stored number in
  the 12th digit (otherwise a ready time exactly on the cutoff can flip from YES to NO);
* ROUNDUP ignores a 1e-9 excess (40 lines / (10 lines/h x 1 h) = 4 people, not 5).

Known workbook artifact: when the slack after adding is exactly zero, Excel shows "-0:00" (its subtraction leaves
a ~1e-12 negative remainder); the app shows "+0:00". The parity test treats both as equal.

## Next phases (not built yet)

Phase 4 drill-down (truck -> work type -> operator), Phase 5 what-if planner (temporary operators, move orders,
delay departure, ship partial), Phase 6 alerts, Phase 7 configuration and audit, Phase 8 production evolution.
