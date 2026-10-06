import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
WORKBOOK = os.path.join(ROOT, "data", "workbook.xlsx")


@pytest.fixture(scope="session")
def results():
    from calculations.engine import run
    return run(WORKBOOK)


@pytest.fixture(scope="session")
def reference():
    from calculations.excel_io import load_reference
    return load_reference(WORKBOOK)
