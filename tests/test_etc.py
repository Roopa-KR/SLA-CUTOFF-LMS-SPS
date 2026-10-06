"""Unit tests for the ETC equation on small hand-made data."""
from types import SimpleNamespace

import pandas as pd
import pytest

from calculations.etc import etc_by_worktype

DAY = 46282.0
T = DAY + 18 / 24  # 18:00


def make_inputs(lm_rows, ti_rows, tc_rows):
    cols_lm = ["SNAPSHOT_NO", "WORKTYPE", "RESOURCENAME", "TOTALTIMEINSEC", "WIPIND", "TRANSACTIONDATE", "CLIENTID", "WHID"]
    cols_ti = ["SNAPSHOT_NO", "WORKTYPE", "TI102ID", "CURRENTSTATUSID", "OPENQTY", "PROCESSEDQTY", "TRANSACTIONDATE", "CLIENTID", "WHID"]
    cols_tc = ["WORKTYPE", "TI102ID", "CURRENTSTATUSID", "PROCESSEDQTY", "TRANSACTIONDATE", "MOVED_TO_C_AT", "CLIENTID", "WHID"]
    return SimpleNamespace(
        config=dict(ANALYSIS_DATE=DAY, CLIENTID_FILTER="SMD", WHID_FILTER=200, WIPIND_WORKING="Y", STATUS_COMPLETED=3),
        snapshots=pd.DataFrame([(1, T)], columns=["SNAPSHOT_NO", "T"]),
        lm=pd.DataFrame(lm_rows, columns=cols_lm), ti=pd.DataFrame(ti_rows, columns=cols_ti),
        tc=pd.DataFrame(tc_rows, columns=cols_tc))


def test_etc_equation_two_pickers():
    # 2 pickers x 1 hour, 10 lines done, 5 open -> rate (10 / 2) x 2 = 10 lines/h -> ETC 0.5 h
    lm = [(1, 1, "A", 3600, "Y", DAY, "SMD", 200), (1, 1, "B", 3600, "Y", DAY, "SMD", 200)]
    ti = [(1, 1, 100 + i, 2, 1, 0, DAY, "SMD", 200) for i in range(5)]
    tc = [(1, 200 + i, 3, 2, DAY, T - 0.01, "SMD", 200) for i in range(10)]
    row = etc_by_worktype(make_inputs(lm, ti, tc)).query("WORKTYPE == 1").iloc[0]
    assert row.TOTALTIMEINHRS == 2
    assert row.TOTALLINES == 10 and row.RESOURCES == 2
    assert row.ACTUALRATELINES == pytest.approx(10)
    assert row.OPENLINES == 5 and row.ETCLINES == pytest.approx(0.5)


def test_lines_picked_after_the_snapshot_do_not_count():
    lm = [(1, 1, "A", 3600, "Y", DAY, "SMD", 200)]
    tc = [(1, 1, 3, 1, DAY, T + 0.01, "SMD", 200)]
    row = etc_by_worktype(make_inputs(lm, [], tc)).query("WORKTYPE == 1").iloc[0]
    assert row.TOTALLINES == 0


def test_no_one_working_gives_null():
    lm = [(1, 1, "A", 3600, "N", DAY, "SMD", 200)]  # logged out: no resources
    ti = [(1, 1, 1, 0, 3, 0, DAY, "SMD", 200)]
    row = etc_by_worktype(make_inputs(lm, ti, [])).query("WORKTYPE == 1").iloc[0]
    assert row.RESOURCES == 0
    assert pd.isna(row.ACTUALRATELINES) and pd.isna(row.ETCLINES)
