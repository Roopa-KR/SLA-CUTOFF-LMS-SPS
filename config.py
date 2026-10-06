"""Application settings, read from .env (python-dotenv)."""
import os

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))


def _path(value: str) -> str:
    return value if os.path.isabs(value) else os.path.join(BASE_DIR, value)


WORKBOOK_PATH = _path(os.getenv("WORKBOOK_PATH", "data/workbook.xlsx"))
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///data/truck_etc.db")
if DATABASE_URL.startswith("sqlite:///") and not os.path.isabs(DATABASE_URL[len("sqlite:///"):]):
    # a relative SQLite path is taken from the project folder, wherever the app is started from
    DATABASE_URL = "sqlite:///" + _path(DATABASE_URL[len("sqlite:///"):]).replace("\\", "/")
DEFAULT_SNAPSHOT = int(os.getenv("DEFAULT_SNAPSHOT", "13"))
