"""Unit tests for the truck rules (status, slack text, cutoff, operators needed)."""
from types import SimpleNamespace

import pandas as pd

from calculations.truck import _truck_answer, _wt_calc, departure_at, hhmm, round_half_away

DAY = 46282.0
H = 1 / 24


def wt_row(open_lines=10, ahead=10, rate=20.0, pp=10.0, res=2, t=DAY + 18 * H, cut=DAY + 19 * H):
    return dict(T=t, PICK_CUTOFF=cut, ACTUALRATELINES=rate, PER_PERSON_RATE=pp, OPEN_LINES=open_lines,
                LINES_AHEAD=ahead, RESOURCES=res)


def test_hhmm_cuts_seconds_like_excel_text():
    assert hhmm(50.9 / 1440) == "0:50"
    assert hhmm(125 / 1440) == "2:05"


def test_round_half_away_from_zero():
    assert round_half_away(2.5) == 3 and round_half_away(-2.5) == -3


def test_departure_change_applies_from_announcement():
    ship = SimpleNamespace(ORIGINAL_DEPARTURE=DAY + 19.25 * H, DEPARTURE_TIME=DAY + 18.75 * H, DEPARTURE_CHANGED_AT=DAY + 18 * H)
    assert departure_at(ship, DAY + 17.75 * H) == ship.ORIGINAL_DEPARTURE
    assert departure_at(ship, DAY + 18 * H) == ship.DEPARTURE_TIME


def test_worktype_meets_and_misses():
    assert _wt_calc(wt_row(ahead=10, rate=20.0))["WT_MEETS"] == "YES"          # 0.5 h, 1 h left
    r = _wt_calc(wt_row(ahead=40, rate=20.0))                                   # 2 h, 1 h left
    assert r["WT_MEETS"] == "NO"
    assert r["OPERATORS_NEEDED"] == 4 and r["ADDITIONAL_OPERATORS"] == 2       # 40 / (10 x 1 h) = 4 people


def test_worktype_no_rate_and_cutoff_passed():
    assert _wt_calc(wt_row(rate=None))["WT_MEETS"] == "UNKNOWN - NO RATE"
    assert _wt_calc(wt_row(t=DAY + 19.5 * H))["WT_MEETS"] == "NO - CUTOFF PASSED"
    assert _wt_calc(wt_row(open_lines=0))["WT_MEETS"] == ""


def _answer(etc_days, t=DAY + 18 * H, dep=DAY + 19.5 * H, open_=10, released=20, missed=None):
    rows = pd.DataFrame([dict(WORKTYPE=1, ADDITIONAL_OPERATORS=3)])
    return _truck_answer(rows, pd.Series([etc_days], dtype=object), t, dep, dep - 0.5 * H, open_, released,
                         after=False, missed_at_departure=missed)


def test_truck_status_rules():
    assert _answer(0.5 * H)["TRUCK_STATUS"] == "ON TIME"
    late = _answer(1.5 * H)
    assert late["TRUCK_STATUS"] == "AT RISK" and late["SLACK"] == "-0:30" and late["ADDITIONAL_OPERATORS_ON_BOTTLENECK"] == 3
    assert _answer(None)["TRUCK_STATUS"] == "NO RATE"
    assert _answer(0.5 * H, open_=0)["TRUCK_STATUS"] == "ALL PICKED"
    assert _answer(0.0, open_=0, released=0)["TRUCK_STATUS"] == "NO WORK RELEASED YET"
    assert _answer(0.5 * H, t=DAY + 19.25 * H)["TRUCK_STATUS"] == "PICK CUTOFF PASSED - LATE"
    assert _answer(0.5 * H, t=DAY + 19.5 * H)["TRUCK_STATUS"] == "DEPARTED - 10 LINES LEFT BEHIND"


def test_departed_status_keeps_the_departure_miss_count_after_late_picks():
    result = _answer(0.0, t=DAY + 20 * H, open_=0, released=20, missed=2)
    assert result["TRUCK_STATUS"] == "DEPARTED - 2 LINES LEFT BEHIND"
