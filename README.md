# Truck ETC LMS (SMD) - first local version

Will each truck's work be picked before its pick cutoff? This app recalculates the Truck ETC of the Excel
workbook in Python, stores the results in SQLite and shows them in a Flask dashboard.

The Excel workbook (`data/workbook.xlsx` = SMD_Truck_ETC_RealDay_2026-09-08.xlsx) is the **golden reference**:
the Python engine reproduces every value of its truck sheets (see "Parity").

**Data: a cleaned subset of the real SMD day 08-Sep-2026** (extract SMD-235, warehouse 100): 1,163 items, 625 work
IDs, 421 customer orders, 33 routes = 33 trucks, and 13 pickers with their real login sessions and pick times. Routes
1001, 1075, and 1085 and all their linked records were removed because their picks occurred after the synthesized
cutoffs/departures. Only the truck departures
(last item release + 60 min, rounded up to 15 min = pick cutoff, + 30 min loading) and dock doors are synthesized.
Operators, familiarity evidence and productivity are never invented. Speeds are real items per observed picker-hour.

SMD structure used: route = truck; a customer order is split into one work ID per region (work ID = order number +
2 digits); a region-6 case-pick work ID can hold items of several customers, and every item keeps its own order and
truck, so all truck calculations count items, not work IDs. Waves are not in the extract.

## Run it in VS Code

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows   (macOS / Linux: source .venv/bin/activate)
pip install -r requirements.txt
python app.py                   # first start builds data/truck_etc.db from the workbook (~5 s)
```

Open http://127.0.0.1:5000 and pick a snapshot (16:15 ... 20:30; default 18:15).
The database is rebuilt automatically whenever `data/workbook.xlsx` changes.

| Task | Command |
| --- | --- |
| Run all tests (calculations, Excel parity, dashboard) | `pytest` |
| Rebuild the database after changing the workbook | `flask --app app init-db` |
| Settings | `.env` (workbook path, database URL, default snapshot) |

Bootstrap and Plotly.js load from their CDN, so the browser needs internet access for styling and charts.

## Project layout (only what is used now)

```text
app.py                  Flask app: dashboard and truck routes + `init-db` command
config.py / .env        settings
calculations/
  excel_io.py           reads inputs and the cached Excel results (openpyxl -> pandas)
  etc.py                the ETC equation per snapshot x work type (ETC_CALC)
  truck.py              truck ETC per work type and per truck (TRUCK_WT_ETC, TRUCK_ETC)
  reallocation.py       qualified existing-operator moves, source-safety checks and after-move ETC
  detail.py             truck drill-down: operators, work types, orders, trends, explanation, alerts
  engine.py             runs the whole calculation
models/
  models.py             SQLAlchemy tables: snapshot, truck_result, worktype_result
  database.py           engine / session, rebuild from the calculation, queries
templates/, static/     Jinja2 + Bootstrap 5 + Plotly.js dashboard
tests/                  pytest: test_etc.py, test_truck.py, test_excel_parity.py, test_detail.py, test_app.py
```

SQLite is reached only through SQLAlchemy (`DATABASE_URL`), so PostgreSQL can replace it later without touching
the calculation code.

## Phase 1 - how the workbook calculates the Truck ETC

**Inputs (workbook sheets)**

| Sheet | Used for |
| --- | --- |
| CONFIG | analysis date, filters (client SMD, warehouse 100, WIPIND Y, status 3), the 18 snapshots |
| LM005S1 | labour segments per snapshot: time, pickers logged in |
| TI102 / TI102C | open lines per snapshot / completed lines with completion time |
| OB_SHIPMENT | trucks: departure, departure change (time it was announced) |
| OB_ORDER, OB_LINE_MAP | order -> truck (with moved orders), pick line -> order |
| TRUCK_CONFIG | LOADING_MINUTES 30 and the observed-evidence-only reallocation rule |
| TRUCK_REALLOCATION | Every at-risk destination, before/after ETC, cutoff, status and no-eligible reason |
| TRUCK_REALLOCATION_MOVES | Accepted moves with source, familiarity, observed pace and source slack evidence |

**Steps (all per snapshot T)**

1. ETC equation per work type (ETC_CALC): TOTALTIMEINHRS = SUM(TOTALTIMEINSEC)/3600; TOTALLINES = completed lines
   (TI102 + TI102C with completion <= T); RESOURCES = distinct pickers with WIPIND Y;
   ACTUALRATELINES = TOTALLINES / TOTALTIMEINHRS x RESOURCES; PER_PERSON_RATE = TOTALLINES / TOTALTIMEINHRS.
2. Truck deadline: departure as known at T (a changed departure applies from DEPARTURE_CHANGED_AT);
   PICK_CUTOFF = departure - LOADING_MINUTES.
3. Per truck and work type (TRUCK_WT_ETC): OPEN_LINES = open TI102 lines of the truck (order's truck as known at T);
   LINES_AHEAD = open lines of that work type on trucks with the same or earlier cutoff; WT_ETC = LINES_AHEAD /
   ACTUALRATELINES; WT_READY_TIME = T + WT_ETC; meets cutoff YES / NO / NO - CUTOFF PASSED / UNKNOWN - NO RATE;
   `WT_STATUS = AT RISK` whenever the calculated ready time exceeds the pick cutoff. OPERATORS_NEEDED =
   ROUNDUP(LINES_AHEAD / (PER_PERSON_RATE x hours to cutoff)), ADDITIONAL = needed - RESOURCES.
4. Per truck (TRUCK_ETC): TRUCK_ETC = largest WT_ETC (BOTTLENECK = that work type; NULL if a needed work type has
   no rate); READY_TIME = T + TRUCK_ETC; SLACK = PICK_CUTOFF - READY_TIME; status (first rule that applies):
   DEPARTED (complete / n lines left behind), NO WORK RELEASED YET, ALL PICKED, PICK CUTOFF PASSED - LATE,
   NO RATE, ON TIME, AT RISK.
5. Existing-operator reallocation, only for an at-risk work type:
   * familiarity comes from either completed TI102C destination lines (actual historical) or the separate
     `OPERATOR_EXPERIENCE` sheet (explicitly labelled **SIMULATED PLANNING ASSUMPTION**);
   * operator pace = that operator's completed lines in the work type / their LM005S1 hours in it by T;
   * destination contribution = actual destination pace when available; otherwise the conservative, data-bounded
     estimate `MIN(actual source pace, actual destination per-person rate)`;
   * destination rate after = team rate before + destination contribution;
   * source rate after = team rate before - operator's observed source pace;
   * source ETC is recalculated for every open source truck, and the move is rejected unless every source cutoff
     remains safe;
   * destination ETC after = LINES_AHEAD / destination rate after. Total headcount is unchanged.

The cleaned real day contains no operator with observed completions in more than one work type, so the application
does not misrepresent cross-training as verified history. A separate deterministic simulated familiarity matrix adds
planning qualifications to the 12 existing operators without creating labor, picks, hours, or headcount. At snapshot 6,
amack, esica and psam are safely reallocated from Prescription to OTC; at snapshot 10, hgroff is safely reallocated
from Cases to OTC. All four moves leave the limiting source truck on time.

**Edge cases handled:** no pickers on a work type (NULL rate), cutoff already passed, departed trucks (lines left
behind), departure changed during the day, order moved to another truck, no work released yet, cancelled lines,
missing cross-training evidence, unsafe source removal and an at-risk destination that remains late.

## Parity with Excel

`tests/test_excel_parity.py` compares every value of ETC_CALC, TRUCK_WT_ETC, TRUCK_ETC and TRUCK_REALLOCATION
(all 18 snapshots) with the cached workbook values: all equal.

Times are kept as Excel serial numbers (days) so comparisons match Excel. Two tiny tolerances are needed and
documented in the code:
* time comparisons allow 1e-9 day, because Python's conversion of a time can differ from Excel's stored number in
  the 12th digit (otherwise a ready time exactly on the cutoff can flip from YES to NO);
* ROUNDUP ignores a 1e-9 excess (40 lines / (10 lines/h x 1 h) = 4 people, not 5);
* TEXT(x, "[h]:mm") is reproduced as Excel does it: rounded to the nearest second, then whole minutes.

NO RATE on the real day means that an area with open work has no usable positive observed rate. Usually nobody is
logged in (Cage, Cooler and Vault before about 17:30; Cooler between batches); early in the day a picker can also be
logged in before completing the first line needed to establish a rate. The app distinguishes those explanations and
shows NO RATE exactly as the workbook does (169 of 594 truck-snapshot rows).

Departed status is frozen at the departure event. `LINES_LEFT_AT_DEPARTURE` counts released lines that were not
picked by departure; later picks cannot change a truck from "DEPARTED - ... LEFT BEHIND" to "DEPARTED COMPLETE".

## Phase 4 - truck drill-down

Click a truck on the dashboard (or open `/truck/SH0908-1010?snapshot=6`). The page shows, for that truck at the snapshot:

* KPIs: lines and pieces picked / open, orders complete, ETC and ready time, bottleneck, operators who picked it,
  extra pickers needed and the result after safe existing-operator reallocation.
* **Why this status**: a sentence built from the numbers (slowest area, lines ahead from earlier trucks, pickers, rate,
  ready time vs cutoff, pickers needed, reallocation result or no-eligible reason; or, for a departed truck, when the last line was
  picked and what was left behind).
* At-risk reallocation: eligible candidates, operators and ETC before/after, selected operator and source, source
  rate/ETC/ready time before and after, source slack rationale, destination familiarity provenance, cutoff, time saved
  and final status. No candidate is moved without evidence.
* Work types: open / ahead / picked, working-assigned-idle pickers, rates, ETC, ready time, meets cutoff and extra pickers;
  chart of each area's finish against the pick cutoff.
* Operators (picked for the truck, hold its work, or work an area it still needs): status (working, assigned not
  started, idle, logged out), lines and pieces for the truck and today, pace vs area average, shorts, work held,
  estimated finish, minutes since last pick, flags; status donut and pace chart. Operator names link to a profile with
  actual productivity, labor time, current/previous assignments, remaining work, ETC, cutoff status, familiarity
  provenance and destination-specific reallocation eligibility. Unavailable productive-only time is stated, not inferred.
* Trends up to the snapshot: open lines and slack, lines picked per 15 min by area, pickers per area.
* Orders on the truck (customer, regions, work IDs, state) and the truck's work IDs (region, customers, items done /
  open, state, pickers; work IDs holding several customers are marked), and alerts with suggested actions.

The drill-down logic is in `calculations/detail.py` (thresholds at the top: assigned-not-started 10 min, no pick 30 min,
low pace below 60% of the area average after 0.5 h). It reads the source rows that `init-db` stores in SQLite
(`src_*` tables) and the stored engine results; `tests/test_detail.py` checks that its totals match the engine.

## Next phases (not built yet)

Work-type page, Phase 5 what-if planner (move orders, delay departure, ship partial), Phase 6 alerts,
Phase 7 configuration and audit, Phase 8 production evolution.
