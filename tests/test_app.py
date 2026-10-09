"""Smoke test: the app builds the SQLite database from the workbook and shows the real day's trucks."""
import os

import pytest


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    os.environ["DATABASE_URL"] = "sqlite:///" + str(tmp_path_factory.mktemp("db") / "test.db").replace("\\", "/")
    import app as app_module
    return app_module.app.test_client()


def test_dashboard_shows_trucks_at_17_30(client):
    html = client.get("/?snapshot=6").get_data(as_text=True)
    assert "SH0908-1010" in html and "AT RISK" in html
    assert "-0:29" in html          # SH0908-1010 slack, same as the workbook
    assert "Truck ETC &middot; SMD warehouse 100" in html
    assert "Workbook analysis day 08-Sep-2026" in html
    assert "NULL" not in html


def test_every_snapshot_renders(client):
    for snap in range(1, 19):
        assert client.get(f"/?snapshot={snap}").status_code == 200


def test_truck_page_explains_the_status(client):
    html = client.get("/truck/SH0908-1010?snapshot=6").get_data(as_text=True)
    assert "Why this status" in html and "OTC is the slowest area" in html
    assert "Work IDs (14)" in html
    assert "built-in method items" not in html
    assert "0 / 1" in html
    assert client.get("/truck/UNKNOWN").status_code == 404


def test_removed_late_routes_are_not_available(client):
    for ship in ("SH0908-1001", "SH0908-1075", "SH0908-1085"):
        assert client.get(f"/truck/{ship}").status_code == 404


def test_truck_pages_render(client):
    for snap in (1, 6, 9, 13, 18):
        for ship in ("SH0908-1042", "SH0908-1027", "SH0908-1888", "SH0908-1981"):
            assert client.get(f"/truck/{ship}?snapshot={snap}").status_code == 200


def test_operator_profile_and_reallocation_provenance_render(client):
    profile = client.get("/operator/hgroff?snapshot=10").get_data(as_text=True)
    assert "Operator hgroff" in profile
    assert "SIMULATED PLANNING ASSUMPTION" in profile
    assert "Actual production rate" in profile
    assert "Productive-only time unavailable" in profile
    truck = client.get("/truck/SH0908-1981?snapshot=10").get_data(as_text=True)
    assert "/operator/hgroff?snapshot=10" in truck
    assert "Eligible candidates" in truck
    assert 'class="candidate-grid"' in truck
    assert 'class="candidate-item"' in truck
    assert "reallocation-wide-table" in truck
    assert "Operator status and pace" not in truck
    assert "Source eligibility / slack" not in truck
    assert "Source before &rarr; after" not in truck
    assert "Destination familiarity" not in truck
    assert "Per picker" not in truck
    assert "operator-table" not in truck
    assert "empirical range" in truck
    assert "Allocated / shortfall" in truck


def test_unfilled_six_picker_gap_is_explicit(client):
    truck = client.get("/truck/SH0908-1981?snapshot=6").get_data(as_text=True)
    assert "0 / 6" in truck
    assert "removing any qualified candidate would put their source work type at risk" in truck
    assert "planning slack" in truck
