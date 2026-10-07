"""Smoke test: the dashboard builds the SQLite database from the workbook and shows the trucks."""
import os

import pytest


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    os.environ["DATABASE_URL"] = "sqlite:///" + str(tmp_path_factory.mktemp("db") / "test.db").replace("\\", "/")
    import app as app_module
    return app_module.app.test_client()


def test_dashboard_shows_trucks_at_19_15(client):
    html = client.get("/?snapshot=13").get_data(as_text=True)
    assert "SH0917-06" in html and "AT RISK" in html
    assert "-0:29" in html          # SH0917-06 slack, same as the workbook


def test_every_snapshot_renders(client):
    for snap in range(1, 19):
        assert client.get(f"/?snapshot={snap}").status_code == 200


def test_truck_page_explains_the_status(client):
    html = client.get("/truck/SH0917-06?snapshot=13").get_data(as_text=True)
    assert "Why this status" in html and "Prescription is the slowest area" in html
    assert client.get("/truck/UNKNOWN").status_code == 404


def test_every_truck_page_renders(client):
    for snap in (1, 7, 13, 18):
        for i in range(1, 11):
            assert client.get(f"/truck/SH0917-{i:02d}?snapshot={snap}").status_code == 200
