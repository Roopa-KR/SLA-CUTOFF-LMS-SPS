"""Run the full Truck ETC calculation from the workbook inputs."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .etc import etc_by_worktype
from .excel_io import load_inputs
from .reallocation import apply_reallocation
from .truck import truck_rows, worktype_rows
from .uncertainty import add_uncertainty


@dataclass
class Results:
    etc: pd.DataFrame          # per snapshot x work type (ETC_CALC)
    worktypes: pd.DataFrame    # per snapshot x truck x work type (TRUCK_WT_ETC)
    trucks: pd.DataFrame       # per snapshot x truck (TRUCK_ETC)
    reallocation: pd.DataFrame # one summary per at-risk snapshot x destination work type
    moves: pd.DataFrame        # one row per accepted existing-operator move
    snapshots: pd.DataFrame
    worktype_names: dict
    inputs: object = None      # the workbook inputs (used to store the drill-down source rows)


def run(workbook_path: str) -> Results:
    inp = load_inputs(workbook_path)
    etc = etc_by_worktype(inp)
    wt = worktype_rows(inp, etc)
    wt = add_uncertainty(inp, wt)
    wt, reallocation, moves = apply_reallocation(inp, wt, inp.worktype_names)
    trucks = truck_rows(inp, wt, after=True)
    return Results(etc, wt, trucks, reallocation, moves, inp.snapshots, inp.worktype_names, inp)
