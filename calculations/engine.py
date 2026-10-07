"""Run the full Truck ETC calculation from the workbook inputs."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .etc import etc_by_worktype
from .excel_io import load_inputs
from .truck import truck_rows, worktype_rows
from .what_if import allocate_temps, apply_temps, temp_need


@dataclass
class Results:
    etc: pd.DataFrame          # per snapshot x work type (ETC_CALC)
    worktypes: pd.DataFrame    # per snapshot x truck x work type (TRUCK_WT_ETC)
    trucks: pd.DataFrame       # per snapshot x truck (TRUCK_ETC)
    temp_need: pd.DataFrame    # per snapshot x work type (TRUCK_TEMP_NEED)
    temp_grid: pd.DataFrame    # per snapshot x temporary operator (TRUCK_TEMP_LM005S1)
    snapshots: pd.DataFrame
    worktype_names: dict
    inputs: object = None      # the workbook inputs (used to store the drill-down source rows)


def run(workbook_path: str) -> Results:
    inp = load_inputs(workbook_path)
    etc = etc_by_worktype(inp)
    wt = worktype_rows(inp, etc)
    need = temp_need(wt)
    temps = inp.temps.rename(columns={w: f"_{w}" for w in range(1, 7)})
    grid = allocate_temps(need, temps)
    wt, need = apply_temps(wt, need, grid, inp.config["TEMP_PACE_PCT"])
    trucks = truck_rows(inp, wt, after=True)
    return Results(etc, wt, trucks, need, grid, inp.snapshots, inp.worktype_names, inp)
