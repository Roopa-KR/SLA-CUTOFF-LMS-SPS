"""The ETC equation per snapshot and work type (sheet ETC_CALC). Single source of truth, unchanged.

    TOTALTIMEINHRS  = SUM(TOTALTIMEINSEC) / 3600
    TOTALLINES      = COUNT(*) of TI102 UNION ALL TI102C rows with CURRENTSTATUSID = 3
    RESOURCES       = COUNT(DISTINCT RESOURCENAME) with WIPIND = 'Y'   (used in place of 'X')
    ACTUALRATELINES = (TOTALLINES / TOTALTIMEINHRS) x RESOURCES
    OPENLINES       = COUNT(DISTINCT TI102ID) where OPENQTY > 0
    ETCLINES        = OPENLINES / ACTUALRATELINES      (hours)

A value that cannot be calculated is None (shown as NULL in the workbook).
"""
from __future__ import annotations

import pandas as pd

WORKTYPES = [1, 2, 3, 4, 5, 6]


def etc_by_worktype(inp) -> pd.DataFrame:
    c = inp.config
    day, client, whid, wip, done = (c["ANALYSIS_DATE"], c["CLIENTID_FILTER"], c["WHID_FILTER"],
                                    c["WIPIND_WORKING"], c["STATUS_COMPLETED"])
    lm = inp.lm[(inp.lm.TRANSACTIONDATE == day) & (inp.lm.CLIENTID == client) & (inp.lm.WHID == whid)]
    ti_day = inp.ti[(inp.ti.TRANSACTIONDATE == day) & (inp.ti.CLIENTID == client) & (inp.ti.WHID == whid)]
    ti_open = inp.ti[(inp.ti.CLIENTID == client) & (inp.ti.WHID == whid) & (inp.ti.OPENQTY > 0)]
    tc = inp.tc[(inp.tc.TRANSACTIONDATE == day) & (inp.tc.CLIENTID == client) & (inp.tc.WHID == whid)
                & (inp.tc.CURRENTSTATUSID == done)]

    secs = lm.groupby(["SNAPSHOT_NO", "WORKTYPE"]).TOTALTIMEINSEC.sum()
    res = lm[lm.WIPIND == wip].groupby(["SNAPSHOT_NO", "WORKTYPE"]).RESOURCENAME.nunique()
    done_ti = ti_day[ti_day.CURRENTSTATUSID == done]
    tl_ti = done_ti.groupby(["SNAPSHOT_NO", "WORKTYPE"]).size()
    tp_ti = done_ti.groupby(["SNAPSHOT_NO", "WORKTYPE"]).PROCESSEDQTY.sum()
    ol = ti_open.groupby(["SNAPSHOT_NO", "WORKTYPE"]).size()
    oq = ti_open.groupby(["SNAPSHOT_NO", "WORKTYPE"]).OPENQTY.sum()

    rows = []
    for snap in inp.snapshots.itertuples():
        tc_t = tc[tc.MOVED_TO_C_AT <= snap.T + 1e-9]
        tl_tc = tc_t.groupby("WORKTYPE").size()
        tp_tc = tc_t.groupby("WORKTYPE").PROCESSEDQTY.sum()
        for wt in WORKTYPES:
            k = (snap.SNAPSHOT_NO, wt)
            hrs = secs.get(k, 0) / 3600
            totallines = int(tl_ti.get(k, 0) + tl_tc.get(wt, 0))
            totalpieces = tp_ti.get(k, 0) + tp_tc.get(wt, 0)
            resources = int(res.get(k, 0))
            rate_l = None if hrs == 0 or resources == 0 else (totallines / hrs) * resources
            rate_p = None if hrs == 0 or resources == 0 else (totalpieces / hrs) * resources
            openlines, openqty = int(ol.get(k, 0)), oq.get(k, 0)
            etc_l = None if openlines == 0 or not rate_l else openlines / rate_l
            etc_p = None if openqty == 0 or not rate_p else openqty / rate_p
            rows.append(dict(SNAPSHOT_NO=snap.SNAPSHOT_NO, T=snap.T, WORKTYPE=wt, TOTALTIMEINHRS=hrs,
                             TOTALLINES=totallines, TOTALPIECES=totalpieces, RESOURCES=resources,
                             ACTUALRATELINES=rate_l, ACTUALRATEPIECES=rate_p, OPENLINES=openlines,
                             OPENQTY=openqty, ETCLINES=etc_l, ETCPIECES=etc_p,
                             PER_PERSON_RATE=(totallines / hrs) if hrs > 0 and totallines > 0 else None))
    return pd.DataFrame(rows)
