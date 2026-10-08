"""Smoke test: the app builds the SQLite database from the workbook and shows the real day's trucks."""
import os

import pytest


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    os.environ["DATABASE_URL"] = "sqlite:///" + str(tmp_path_factory.mktemp("db") / "test.db").replace("\\", "/")
    import app as app_module
    return app_module.app.test_client()


def test_dashboard_shows_trucks_at_18_15(client):
    html = client.get("/?snapshot=9").get_data(as_text=True)
    assert "SH0908-1001" in html and "AT RISK" in html
    assert "-0:56" in html          # SH0908-1001 slack, same as the workbook


def test_every_snapshot_renders(client):
    for snap in range(1, 19):
        assert client.get(f"/?snapshot={snap}").status_code == 200


def test_truck_page_explains_the_status(client):
    html = client.get("/truck/SH0908-1001?snapshot=9").get_data(as_text=True)
    assert "Why this status" in html and "Cooler is the slowest area" in html
    assert "Work IDs (83)" in html
    assert client.get("/truck/UNKNOWN").status_code == 404


def test_truck_pages_render(client):
    for snap in (1, 6, 9, 13, 18):
        for ship in ("SH0908-1001", "SH0908-1027", "SH0908-1888", "SH0908-1981"):
            assert client.get(f"/truck/{ship}?snapshot={snap}").status_code == 200
