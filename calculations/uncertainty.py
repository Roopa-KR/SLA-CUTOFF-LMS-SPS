"""Empirical ETC ranges derived only from productivity already observed in the data.

The point ETC remains the existing cumulative-average calculation.  The range is
not a statistical confidence interval: it applies the lowest and highest positive
operator pace observed for the work type by the snapshot to the current resource
count.  The point team rate is included in the envelope.  This gives supervisors
an evidence-backed sensitivity range without inventing a fixed buffer or confidence
percentage.  Unmeasured travel, congestion, breaks and equipment delays remain
explicitly outside the range.
"""
from __future__ import annotations

import math

import pandas as pd

from .truck import ge, le


def _operator_paces(inp, snapshot_no: int, t: float) -> dict[int, list[float]]:
    lm = inp.lm[inp.lm.SNAPSHOT_NO == snapshot_no]
    done = inp.tc[(inp.tc.TRANSACTIONDATE == inp.config["ANALYSIS_DATE"])
                  & (inp.tc.MOVED_TO_C_AT <= t + 1e-9)]
    hours = lm.groupby(["RESOURCENAME", "WORKTYPE"]).TOTALTIMEINSEC.sum() / 3600
    lines = done.groupby(["RESOURCENAME", "WORKTYPE"]).size()
    out: dict[int, list[float]] = {}
    for operator, worktype in set(hours.index) | set(lines.index):
        h, n = float(hours.get((operator, worktype), 0)), int(lines.get((operator, worktype), 0))
        if h > 0 and n > 0:
            out.setdefault(int(worktype), []).append(n / h)
    return out


def add_uncertainty(inp, wt: pd.DataFrame) -> pd.DataFrame:
    """Add empirical rate/ETC bounds and a conservative planning classification."""
    wt = wt.copy()
    evidence: dict[tuple[int, int], list[float]] = {}
    snapshots = (inp.snapshots[["SNAPSHOT_NO", "T"]].drop_duplicates()
                 if hasattr(inp, "snapshots") else wt[["SNAPSHOT_NO", "T"]].drop_duplicates())
    for snap in snapshots.itertuples():
        for worktype, paces in _operator_paces(inp, int(snap.SNAPSHOT_NO), float(snap.T)).items():
            evidence[(int(snap.SNAPSHOT_NO), worktype)] = paces

    output = []
    for row in wt.to_dict("records"):
        paces = evidence.get((int(row["SNAPSHOT_NO"]), int(row["WORKTYPE"])), [])
        point = row.get("ACTUALRATELINES")
        resources = int(row.get("RESOURCES", 0))
        if isinstance(point, (int, float)) and not pd.isna(point) and point > 0:
            observed_team = [resources * p for p in paces] if resources > 0 else []
            rate_low = min([float(point)] + observed_team)
            rate_high = max([float(point)] + observed_team)
        else:
            rate_low = rate_high = None

        open_lines = int(row.get("OPEN_LINES", 0))
        ahead = int(row.get("LINES_AHEAD", 0))
        t, cutoff = row["T"], row["PICK_CUTOFF"]
        if open_lines <= 0:
            etc_low = etc_high = ready_early = ready_late = ""
            planning_meets = ""
        elif ge(t, cutoff):
            etc_low = etc_high = ready_early = ready_late = None
            planning_meets = "NO - CUTOFF PASSED"
        elif rate_low is None or rate_low <= 0:
            etc_low = etc_high = ready_early = ready_late = None
            planning_meets = "UNKNOWN - NO RATE"
        else:
            etc_low = ahead / rate_high / 24
            etc_high = ahead / rate_low / 24
            ready_early, ready_late = t + etc_low, t + etc_high
            planning_meets = "YES" if le(ready_late, cutoff) else "NO"

        low_pp = rate_low / resources if rate_low and resources else None
        if planning_meets == "NO" and low_pp and cutoff > t:
            required = math.ceil(ahead / (low_pp * (cutoff - t) * 24) - 1e-9)
            additional = max(0, required - resources)
        else:
            required = additional = ""

        count = len(paces)
        if count >= 2:
            evidence_text = (f"Empirical envelope from {count} measured operator paces in this work type; "
                             "not a probabilistic confidence interval")
        elif count == 1:
            evidence_text = ("Limited empirical envelope from one measured operator pace plus the current team rate; "
                             "variability is not well established")
        else:
            evidence_text = ("No operator-level variability evidence; range equals the current observed team-rate estimate")
        row.update(OBSERVED_PACE_COUNT=count, EMPIRICAL_PACE_LOW=min(paces) if paces else None,
                   EMPIRICAL_PACE_HIGH=max(paces) if paces else None,
                   RATE_LOW=rate_low, RATE_HIGH=rate_high, WT_ETC_LOW=etc_low, WT_ETC_HIGH=etc_high,
                   WT_READY_EARLY=ready_early, WT_READY_LATE=ready_late,
                   WT_PLANNING_MEETS=planning_meets,
                   WT_PLANNING_STATUS={"YES": "ON TIME", "NO": "AT RISK",
                                       "NO - CUTOFF PASSED": "CUTOFF PASSED",
                                       "UNKNOWN - NO RATE": "NO RATE", "": ""}[planning_meets],
                   OPERATORS_NEEDED_CONSERVATIVE=required,
                   ADDITIONAL_OPERATORS_CONSERVATIVE=additional,
                   UNCERTAINTY_EVIDENCE=evidence_text)
        output.append(row)
    return pd.DataFrame(output)
