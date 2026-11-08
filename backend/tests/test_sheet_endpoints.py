"""Endpoint tests for /api/v1/tools/spreadsheets (FakeSheetsProvider-backed)."""

import base64

import pytest
from test_sheet_service_data import SHEET_URL, FakeSheetsProvider

from app.services import document_service, sheet_service

BASE = "/api/v1/tools"


@pytest.fixture()
def fake(monkeypatch):
    provider = FakeSheetsProvider()
    monkeypatch.setattr(sheet_service, "_provider", lambda db, ctx: provider)
    return provider


class FakeDocsProvider:
    """Minimal docs provider stand-in for the create-with-folder_id test."""

    def __init__(self):
        self.moves: list[tuple[str, str, bool]] = []

    def create_document(self, title):
        return {"documentId": "doc1", "title": title, "revisionId": "rev-1"}

    def batch_update(self, document_id, requests):
        return {}

    def execute_plan(self, document_id, steps, start_cursor, theme):
        return start_cursor, "rev-2"

    def get_document_fresh(self, document_id):
        return {"revisionId": "rev-2"}

    def move_file(self, file_id, add_parent_id, remove_current=True):
        from app.document_engine.providers.google_docs import DocNotFound

        if add_parent_id == "missing":
            raise DocNotFound(add_parent_id)
        self.moves.append((file_id, add_parent_id, remove_current))


@pytest.fixture()
def docs_fake(monkeypatch):
    provider = FakeDocsProvider()
    monkeypatch.setattr(document_service, "_provider", lambda db, ctx: provider)
    return provider


def test_create_spreadsheet_endpoint(client, active_key, fake):
    res = client.post(
        f"{BASE}/spreadsheets",
        json={"title": "Quarterly Plan", "sheets": ["Budget", "Notes"]},
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 201
    body = res.json()
    assert body["spreadsheet_id"] == "sheet1"
    assert body["url"] == SHEET_URL
    assert body["sheets"] == ["Budget", "Notes"]
    assert "Quarterly Plan" in body["summary"]


def test_search_spreadsheets_endpoint(client, active_key, fake):
    res = client.post(
        f"{BASE}/spreadsheets/search",
        json={"query": "quarterly", "folder_id": "folder1", "limit": 5},
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 200
    spreadsheets = res.json()["spreadsheets"]
    assert len(spreadsheets) == 1
    assert spreadsheets[0]["id"] == "sheet1"
    assert spreadsheets[0]["name"] == "Test spreadsheet"
    assert spreadsheets[0]["url"] == SHEET_URL
    assert fake.calls[-1] == ("search_spreadsheets", "quarterly", 5, "folder1")


def test_get_spreadsheet_meta(client, active_key, fake):
    res = client.get(
        f"{BASE}/spreadsheets/sheet1", headers={"Authorization": f"Bearer {active_key}"}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["spreadsheet_id"] == "sheet1"
    assert body["title"] == "Test spreadsheet"
    assert body["url"] == SHEET_URL
    assert body["modified_time"] == "2026-11-01T00:00:00Z"
    assert [sheet["name"] for sheet in body["sheets"]] == ["Sheet1", "Data"]
    assert body["sheets"][0] == {
        "name": "Sheet1",
        "sheet_id": 0,
        "rows": 100,
        "columns": 26,
        "hidden": False,
    }


def test_read_values_json(client, active_key, fake):
    fake.data["Data!A1:B2"] = [["h1", "h2"], [1, 2]]
    res = client.get(
        f"{BASE}/spreadsheets/sheet1/values",
        params={"sheet": "Data", "range": "A1:B2"},
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["spreadsheet_id"] == "sheet1"
    assert body["sheet"] == "Data"
    assert body["range"] == "Data!A1:B2"
    assert body["mode"] == "values"
    assert body["values"] == [["h1", "h2"], [1, 2]]
    assert body["formulas"] is None and body["csv"] is None and body["markdown"] is None


def test_read_values_csv(client, active_key, fake):
    fake.data["Data"] = [["Name", "Value"], ["x,y", 5]]
    res = client.get(
        f"{BASE}/spreadsheets/sheet1/values",
        params={"sheet": "Data", "format": "csv"},
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 200
    assert res.json()["csv"] == 'Name,Value\r\n"x,y",5\r\n'


def test_write_then_read_round_trip(client, active_key, fake):
    res = client.post(
        f"{BASE}/spreadsheets/sheet1/values",
        json={"sheet": "Data", "range": "A1:B2", "rows": [["a", 1], ["b", 2]]},
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["updated_cells"] == 4
    assert body["sheet"] == "Data"
    assert body["range"] == "Data!A1:B2"
    assert "Updated 4 cells" in body["summary"]

    res = client.get(
        f"{BASE}/spreadsheets/sheet1/values",
        params={"sheet": "Data", "range": "A1:B2"},
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.json()["values"] == [["a", 1], ["b", 2]]


def test_write_unknown_sheet_lists_available(client, active_key, fake):
    res = client.post(
        f"{BASE}/spreadsheets/sheet1/values",
        json={"sheet": "Missing", "range": "A1", "rows": [[1]]},
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 404
    body = res.json()
    assert body["error"] == "sheet_not_found"
    assert "Sheet1" in body["detail"] and "Data" in body["detail"]
    assert not any(call[0] == "update_values" for call in fake.calls)


def test_clear_confirm_two_step(client, active_key, fake):
    fake.data["Data!A1:B3"] = [["x", "y"], [1, 2], [3, 4]]
    res = client.post(
        f"{BASE}/spreadsheets/sheet1/values/clear",
        json={"sheet": "Data", "range": "A1:B3"},
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 422
    body = res.json()
    assert body["error"] == "confirmation_required"
    assert body["preview"] == {
        "sheet": "Data",
        "range": "Data!A1:B3",
        "estimated_cells": 6,
    }
    assert not any(call[0] == "clear_values" for call in fake.calls)
    assert "Data!A1:B3" in fake.data  # nothing was cleared

    res = client.post(
        f"{BASE}/spreadsheets/sheet1/values/clear",
        json={"sheet": "Data", "range": "A1:B3", "confirm": True},
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 200
    assert "Cleared Data!A1:B3" in res.json()["summary"]
    assert "Data!A1:B3" not in fake.data


def test_find_replace_confirm_two_step(client, active_key, fake):
    fake.data["Data"] = [["Alpha", "alpha"], ["ALPHA"]]
    res = client.post(
        f"{BASE}/spreadsheets/sheet1/find-replace",
        json={"find": "alpha", "sheet": "Data"},
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 422
    body = res.json()
    assert body["error"] == "confirmation_required"
    assert body["preview"]["occurrences"] == 3
    assert not any(call[0] == "find_replace" for call in fake.calls)
    assert fake.data["Data"] == [["Alpha", "alpha"], ["ALPHA"]]

    res = client.post(
        f"{BASE}/spreadsheets/sheet1/find-replace",
        json={"find": "alpha", "replace": "beta", "sheet": "Data", "confirm": True},
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["occurrences_changed"] == 3
    assert fake.data["Data"] == [["beta", "beta"], ["beta"]]


def test_append_values_endpoint(client, active_key, fake):
    res = client.post(
        f"{BASE}/spreadsheets/sheet1/values/append",
        json={"sheet": "Data", "rows": [[1, 2]]},
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["updated_rows"] == 1
    assert body["sheet"] == "Data"
    assert body["range"] == "Data!A2:B2"


def test_add_rename_delete_tab(client, active_key, fake):
    res = client.post(
        f"{BASE}/spreadsheets/sheet1/sheets",
        json={"name": "Extra", "rows": 50, "cols": 5},
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["sheet"] == "Extra"
    assert "Added tab 'Extra'" in body["summary"]

    res = client.patch(
        f"{BASE}/spreadsheets/sheet1/sheets/Data",
        json={"new_name": "Renamed"},
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["sheet"] == "Renamed"
    assert "Renamed tab 'Data' to 'Renamed'" in body["summary"]

    res = client.request(
        "DELETE",
        f"{BASE}/spreadsheets/sheet1/sheets/Data",
        json={},
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 422
    body = res.json()
    assert body["error"] == "confirmation_required"
    assert body["preview"]["sheet"] == "Data"
    assert not any(
        call[0] == "batch_update" and any("deleteSheet" in r for r in call[2])
        for call in fake.calls
    )

    res = client.request(
        "DELETE",
        f"{BASE}/spreadsheets/sheet1/sheets/Data",
        json={"confirm": True},
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 200
    assert "Deleted tab 'Data'" in res.json()["summary"]
    assert any(
        call[0] == "batch_update" and any("deleteSheet" in r for r in call[2])
        for call in fake.calls
    )


def test_delete_spreadsheet_confirm_two_step(client, active_key, fake):
    res = client.post(
        f"{BASE}/spreadsheets/sheet1/delete",
        json={},
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 422
    body = res.json()
    assert body["error"] == "confirmation_required"
    assert "trash" in body["preview"]["effect"]
    assert fake.trashed is False

    res = client.post(
        f"{BASE}/spreadsheets/sheet1/delete",
        json={"confirm": True},
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 200
    assert "trash" in res.json()["summary"]
    assert fake.trashed is True


def test_export_endpoint(client, active_key, fake):
    res = client.get(
        f"{BASE}/spreadsheets/sheet1/export",
        params={"format": "xlsx"},
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["format"] == "xlsx"
    assert "spreadsheetml" in body["mime"]
    assert body["filename"] == "spreadsheet.xlsx"
    assert base64.b64decode(body["content_base64"]) == b"%PDF-1.4 fake export"

    res = client.get(
        f"{BASE}/spreadsheets/sheet1/export",
        params={"format": "bogus"},
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 422
    assert res.json()["error"] == "invalid_request"


def test_share_and_list_permissions(client, active_key, fake):
    res = client.post(
        f"{BASE}/spreadsheets/sheet1/share",
        json={"email": "partner@example.com", "role": "writer"},
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["spreadsheet_id"] == "sheet1"
    assert body["permission_id"] == "perm-123"
    assert fake.calls[-1] == ("share_file", "sheet1", "partner@example.com", "writer", True)

    res = client.get(
        f"{BASE}/spreadsheets/sheet1/share", headers={"Authorization": f"Bearer {active_key}"}
    )
    assert res.status_code == 200
    permissions = res.json()["permissions"]
    assert permissions[0]["email"] == "owner@example.com"
    assert permissions[0]["role"] == "owner"


def test_rename_move_copy_restore(client, active_key, fake):
    res = client.post(
        f"{BASE}/spreadsheets/sheet1/rename",
        json={"title": "Quarterly Plan v2"},
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 200
    assert fake.file_name == "Quarterly Plan v2"
    assert "Quarterly Plan v2" in res.json()["summary"]

    res = client.post(
        f"{BASE}/spreadsheets/sheet1/move",
        json={"folder_id": "folder1"},
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 200
    assert fake.calls[-1] == ("move_file", "sheet1", "folder1", True)

    res = client.post(
        f"{BASE}/spreadsheets/sheet1/copy",
        json={"title": "Copy of Plan"},
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 201
    assert res.json()["spreadsheet_id"] == "sheet1-copy"

    res = client.post(
        f"{BASE}/spreadsheets/sheet1/restore",
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 200
    assert "restored" in res.json()["summary"]


def test_create_document_with_folder_id(client, active_key, docs_fake):
    res = client.post(
        f"{BASE}/documents",
        json={"title": "Report", "markdown": "# Report\n\nBody", "folder_id": "folder1"},
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 201
    assert docs_fake.moves == [("doc1", "folder1", True)]
