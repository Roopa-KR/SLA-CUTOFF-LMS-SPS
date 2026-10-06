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
