"""Helpers to compare engine values with the cached Excel values."""
import math

import pandas as pd

TOL = 1e-6  # days (~0.09 s) or units


def norm(v):
    if v is None or v == "" or v == "NULL" or (isinstance(v, float) and math.isnan(v)):
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if v == "-0:00":  # zero slack: Excel's subtraction can leave a ~1e-12 negative remainder
        return "+0:00"
    return v


def same(a, b) -> bool:
    a, b = norm(a), norm(b)
    if isinstance(a, float) and isinstance(b, float):
        return abs(a - b) <= TOL * max(1.0, abs(a))
    return a == b


def mismatches(excel: pd.Series, python, limit=5):
    bad = [(i, e, p) for i, (e, p) in enumerate(zip(excel, python)) if not same(e, p)]
    return len(bad), bad[:limit]
