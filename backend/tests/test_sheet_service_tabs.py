"""Service tests for tab management and spreadsheet file-level operations."""

import base64
from types import SimpleNamespace

import pytest
from googleapiclient.errors import HttpError
from test_sheet_service_data import SHEET_URL, FakeSheetsProvider

from app.core.tool_errors import ToolApiError
from app.services import sheet_service as sheets

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture
def fake(monkeypatch):
    provider = FakeSheetsProvider()
    monkeypatch.setattr(sheets, "_provider", lambda db, tool_ctx: provider)
    return provider


# ---- add_sheet ----


def test_add_sheet_success(fake):
    result = sheets.add_sheet(None, None, "sheet1", "Budget", 200, 30)
    assert result["spreadsheet_id"] == "sheet1"
    assert result["sheet"] == "Budget"
    assert "Budget" in result["summary"]
    assert fake.calls == [
        ("get_spreadsheet", "sheet1"),
        (
            "batch_update",
            "sheet1",
            [
                {
                    "addSheet": {
                        "properties": {
                            "title": "Budget",
                            "gridProperties": {"rowCount": 200, "columnCount": 30},
                        }
                    }
                }
            ],
        ),
    ]


def test_add_sheet_defaults(fake):
    sheets.add_sheet(None, None, "sheet1", "New")
    assert fake.calls[-1] == (
        "batch_update",
        "sheet1",
        [
            {
                "addSheet": {
                    "properties": {
                        "title": "New",
                        "gridProperties": {"rowCount": 1000, "columnCount": 26},
                    }
                }
            }
        ],
    )


def test_add_sheet_rejects_duplicate_name_naming_existing_tabs(fake):
    with pytest.raises(ToolApiError) as exc:
        sheets.add_sheet(None, None, "sheet1", "DATA")
    assert exc.value.code == "invalid_request"
    assert "a tab named 'DATA' already exists" in exc.value.message
    assert "Sheet1" in exc.value.message and "Data" in exc.value.message
    assert not any(call[0] == "batch_update" for call in fake.calls)


@pytest.mark.parametrize("name", ["", "x" * 101, None, 42])
def test_add_sheet_rejects_bad_name(fake, name):
    with pytest.raises(ToolApiError) as exc:
        sheets.add_sheet(None, None, "sheet1", name)
    assert exc.value.code == "invalid_request"
    assert "1-100" in exc.value.message
    assert fake.calls == []


@pytest.mark.parametrize("rows", [0, 50_001, "big", None, True])
def test_add_sheet_rejects_out_of_bounds_rows(fake, rows):
    with pytest.raises(ToolApiError) as exc:
        sheets.add_sheet(None, None, "sheet1", "New", rows=rows)
    assert exc.value.code == "invalid_request"
    assert "rows" in exc.value.message
    assert fake.calls == []


@pytest.mark.parametrize("cols", [0, 501, "wide", None, True])
def test_add_sheet_rejects_out_of_bounds_cols(fake, cols):
    with pytest.raises(ToolApiError) as exc:
        sheets.add_sheet(None, None, "sheet1", "New", cols=cols)
    assert exc.value.code == "invalid_request"
    assert "cols" in exc.value.message
    assert fake.calls == []


# ---- update_sheet ----


def test_update_sheet_rename_and_hide(fake):
    result = sheets.update_sheet(None, None, "sheet1", "Data", new_name="Records", hidden=True)
    assert result["spreadsheet_id"] == "sheet1"
    assert result["sheet"] == "Records"
    assert fake.calls[-1] == (
        "batch_update",
        "sheet1",
        [
            {
                "updateSheetProperties": {
                    "properties": {"sheetId": 1, "title": "Records", "hidden": True},
                    "fields": "title,hidden",
                }
            }
        ],
    )


def test_update_sheet_rename_only(fake):
    result = sheets.update_sheet(None, None, "sheet1", "Data", new_name="Records")
    assert result["sheet"] == "Records"
    assert fake.calls[-1] == (
        "batch_update",
        "sheet1",
        [
            {
                "updateSheetProperties": {
                    "properties": {"sheetId": 1, "title": "Records"},
                    "fields": "title",
                }
            }
        ],
    )


def test_update_sheet_hide_only(fake):
    result = sheets.update_sheet(None, None, "sheet1", "Data", hidden=True)
    assert result["sheet"] == "Data"
    assert fake.calls[-1] == (
        "batch_update",
        "sheet1",
        [
            {
                "updateSheetProperties": {
                    "properties": {"sheetId": 1, "hidden": True},
                    "fields": "hidden",
                }
            }
        ],
    )


def test_update_sheet_requires_a_change(fake):
    with pytest.raises(ToolApiError) as exc:
        sheets.update_sheet(None, None, "sheet1", "Data")
    assert exc.value.code == "invalid_request"
    assert fake.calls == []


def test_update_sheet_rejects_duplicate_new_name(fake):
    with pytest.raises(ToolApiError) as exc:
        sheets.update_sheet(None, None, "sheet1", "Data", new_name="SHEET1")
    assert exc.value.code == "invalid_request"
    assert "a tab named 'SHEET1' already exists" in exc.value.message
    assert not any(call[0] == "batch_update" for call in fake.calls)


def test_update_sheet_allows_case_only_rename_of_same_tab(fake):
    result = sheets.update_sheet(None, None, "sheet1", "Data", new_name="DATA")
    assert result["sheet"] == "DATA"
    assert fake.calls[-1][2][0]["updateSheetProperties"]["properties"]["title"] == "DATA"


def test_update_sheet_rejects_bad_new_name(fake):
    with pytest.raises(ToolApiError) as exc:
        sheets.update_sheet(None, None, "sheet1", "Data", new_name="x" * 101)
    assert exc.value.code == "invalid_request"
    assert fake.calls == []


def test_update_sheet_rejects_non_bool_hidden(fake):
    with pytest.raises(ToolApiError) as exc:
        sheets.update_sheet(None, None, "sheet1", "Data", hidden="yes")
    assert exc.value.code == "invalid_request"
    assert fake.calls == []


def test_update_sheet_unknown_tab_lists_available(fake):
    with pytest.raises(ToolApiError) as exc:
        sheets.update_sheet(None, None, "sheet1", "Missing", hidden=True)
    assert exc.value.code == "sheet_not_found"
    assert not any(call[0] == "batch_update" for call in fake.calls)


# ---- delete_sheet ----


def test_delete_sheet_requires_confirm(fake):
    with pytest.raises(ToolApiError) as exc:
        sheets.delete_sheet(None, None, "sheet1", "Data")
    assert (exc.value.code, exc.value.status_code) == ("confirmation_required", 422)
    assert exc.value.extra["preview"] == {"sheet": "Data", "rows": 50, "columns": 10}
    assert not any(call[0] == "batch_update" for call in fake.calls)


def test_delete_last_tab_rejected(fake):
    del fake.meta["sheets"][1]
    with pytest.raises(ToolApiError) as exc:
        sheets.delete_sheet(None, None, "sheet1", "Sheet1", confirm=True)
    assert exc.value.code == "invalid_request"
    assert "cannot delete the only tab in the spreadsheet" in exc.value.message
    assert not any(call[0] == "batch_update" for call in fake.calls)


def test_delete_sheet_confirmed(fake):
    result = sheets.delete_sheet(None, None, "sheet1", "Data", confirm=True)
    assert result["spreadsheet_id"] == "sheet1"
    assert result["sheet"] == "Data"
    assert "Data" in result["summary"]
    assert fake.calls[-1] == ("batch_update", "sheet1", [{"deleteSheet": {"sheetId": 1}}])


def test_delete_sheet_unknown_tab_lists_available(fake):
    with pytest.raises(ToolApiError) as exc:
        sheets.delete_sheet(None, None, "sheet1", "Missing", confirm=True)
    assert exc.value.code == "sheet_not_found"
    assert not any(call[0] == "batch_update" for call in fake.calls)


# ---- create_spreadsheet ----


def test_create_spreadsheet_with_folder_and_tabs(fake):
    result = sheets.create_spreadsheet(
        None, None, "New title", ["Expenses", "Income"], folder_id="folder1"
    )
    assert result["spreadsheet_id"] == "sheet1"
    assert result["url"] == SHEET_URL
    assert result["title"] == "New title"
    assert result["sheets"] == ["Expenses", "Income"]
    assert fake.calls == [
        ("create_spreadsheet", "New title", ["Expenses", "Income"]),
        ("move_file", "sheet1", "folder1", True),
    ]


def test_create_spreadsheet_without_optional_args(fake):
    result = sheets.create_spreadsheet(None, None, "Blank")
    assert result["sheets"] == ["Sheet1"]
    assert fake.calls == [("create_spreadsheet", "Blank", [])]


def test_create_spreadsheet_rejects_duplicate_tab_names(fake):
    with pytest.raises(ToolApiError) as exc:
        sheets.create_spreadsheet(None, None, "T", ["Data", "data"])
    assert exc.value.code == "invalid_request"
    assert "a tab named 'data' already exists" in exc.value.message
    assert fake.calls == []


def test_create_spreadsheet_rejects_too_many_tabs(fake):
    with pytest.raises(ToolApiError) as exc:
        sheets.create_spreadsheet(None, None, "T", [f"Tab {i}" for i in range(51)])
    assert exc.value.code == "invalid_request"
    assert "50" in exc.value.message
    assert fake.calls == []


@pytest.mark.parametrize("name", ["", "x" * 101, None, 42])
def test_create_spreadsheet_rejects_bad_tab_name(fake, name):
    with pytest.raises(ToolApiError) as exc:
        sheets.create_spreadsheet(None, None, "T", ["Ok", name])
    assert exc.value.code == "invalid_request"
    assert fake.calls == []


def test_create_spreadsheet_rejects_missing_folder(fake):
    with pytest.raises(ToolApiError) as exc:
        sheets.create_spreadsheet(None, None, "T", ["Tab"], folder_id="nope")
    assert exc.value.code == "invalid_request"
    assert "nope" in exc.value.message
    assert fake.calls[-1][0] == "move_file"


# ---- search_spreadsheets ----


def test_search_spreadsheets_passes_folder(fake):
    result = sheets.search_spreadsheets(None, None, "annual", 10, folder_id="folder1")
    assert result[0]["id"] == "sheet1"
    assert result[0]["url"] == SHEET_URL
    assert fake.calls == [("search_spreadsheets", "annual", 10, "folder1")]


def test_search_spreadsheets_defaults(fake):
    sheets.search_spreadsheets(None, None)
    assert fake.calls == [("search_spreadsheets", "", 10, None)]


# ---- get_spreadsheet ----


def test_get_spreadsheet_metadata(fake):
    result = sheets.get_spreadsheet(None, None, "sheet1")
    assert result["spreadsheet_id"] == "sheet1"
    assert result["title"] == "Test spreadsheet"
    assert result["url"] == SHEET_URL
    assert result["modified_time"] == "2026-11-01T00:00:00Z"
    assert result["sheets"] == [
        {"name": "Sheet1", "sheet_id": 0, "rows": 100, "columns": 26, "hidden": False},
        {"name": "Data", "sheet_id": 1, "rows": 50, "columns": 10, "hidden": False},
    ]
    assert [call[0] for call in fake.calls] == ["get_spreadsheet", "get_file_metadata"]


def test_get_spreadsheet_not_found(fake):
    with pytest.raises(ToolApiError) as exc:
        sheets.get_spreadsheet(None, None, "missing")
    assert (exc.value.code, exc.value.status_code) == ("spreadsheet_not_found", 404)


# ---- trash / restore / rename / move / copy / share / permissions / export ----


def test_trash_spreadsheet_requires_confirm(fake):
    with pytest.raises(ToolApiError) as exc:
        sheets.trash_spreadsheet(None, None, "sheet1")
    assert (exc.value.code, exc.value.status_code) == ("confirmation_required", 422)
    assert exc.value.extra["preview"] == {
        "operation": "delete",
        "spreadsheet_id": "sheet1",
        "effect": "Moves the spreadsheet to Google Drive trash (recoverable in Drive).",
    }
    assert not any(call[0] == "trash_document" for call in fake.calls)


def test_trash_spreadsheet_confirmed(fake):
    result = sheets.trash_spreadsheet(None, None, "sheet1", True)
    assert result["spreadsheet_id"] == "sheet1"
    assert result["url"] == SHEET_URL
    assert result["summary"] == "Spreadsheet moved to Google Drive trash"
    assert fake.calls[-1] == ("trash_document", "sheet1")


def test_trash_spreadsheet_not_found(fake):
    with pytest.raises(ToolApiError) as exc:
        sheets.trash_spreadsheet(None, None, "missing", True)
    assert (exc.value.code, exc.value.status_code) == ("spreadsheet_not_found", 404)
    assert "missing" in exc.value.message


def test_restore_spreadsheet(fake):
    result = sheets.restore_spreadsheet(None, None, "sheet1")
    assert result["summary"] == "Spreadsheet restored from Google Drive trash"
    assert fake.calls[-1] == ("untrash_document", "sheet1")


def test_rename_spreadsheet(fake):
    result = sheets.rename_spreadsheet(None, None, "sheet1", "Annual")
    assert result["summary"] == "Renamed spreadsheet to 'Annual'"
    assert result["url"] == SHEET_URL
    assert fake.calls[-1] == ("rename_file", "sheet1", "Annual")
    assert fake.file_name == "Annual"


def test_move_spreadsheet(fake):
    result = sheets.move_spreadsheet(None, None, "sheet1", "folder1")
    assert result["summary"] == "Moved spreadsheet into folder folder1"
    assert fake.calls[-1] == ("move_file", "sheet1", "folder1", True)


def test_move_spreadsheet_missing_folder(fake):
    with pytest.raises(ToolApiError) as exc:
        sheets.move_spreadsheet(None, None, "sheet1", "nope")
    assert exc.value.code == "invalid_request"
    assert "nope" in exc.value.message
    assert fake.calls[-1][0] == "move_file"


def test_move_spreadsheet_not_found(fake):
    with pytest.raises(ToolApiError) as exc:
        sheets.move_spreadsheet(None, None, "missing", "folder1")
    assert (exc.value.code, exc.value.status_code) == ("spreadsheet_not_found", 404)


def test_copy_spreadsheet(fake):
    result = sheets.copy_spreadsheet(None, None, "sheet1", "Annual copy")
    assert result["spreadsheet_id"] == "sheet1-copy"
    assert result["url"] == "https://docs.google.com/spreadsheets/d/sheet1-copy/edit"
    assert result["summary"] == "Copied spreadsheet as 'Annual copy'"
    assert fake.calls == [
        ("get_spreadsheet", "sheet1"),
        ("copy_spreadsheet", "sheet1", "Annual copy"),
    ]


def test_copy_spreadsheet_not_found(fake):
    with pytest.raises(ToolApiError) as exc:
        sheets.copy_spreadsheet(None, None, "missing")
    assert (exc.value.code, exc.value.status_code) == ("spreadsheet_not_found", 404)


def test_share_spreadsheet(fake):
    result = sheets.share_spreadsheet(None, None, "sheet1", "a@b.com", "writer")
    assert result["spreadsheet_id"] == "sheet1"
    assert result["permission_id"] == "perm-123"
    assert result["email"] == "a@b.com"
    assert result["role"] == "writer"
    assert result["summary"] == "Shared spreadsheet with a@b.com as writer"
    assert fake.calls == [
        ("get_spreadsheet", "sheet1"),
        ("share_file", "sheet1", "a@b.com", "writer", True),
    ]


def test_share_spreadsheet_not_found(fake):
    with pytest.raises(ToolApiError) as exc:
        sheets.share_spreadsheet(None, None, "missing", "a@b.com", "writer")
    assert (exc.value.code, exc.value.status_code) == ("spreadsheet_not_found", 404)


def test_list_spreadsheet_permissions(fake):
    result = sheets.list_spreadsheet_permissions(None, None, "sheet1")
    assert result == [
        {"id": "perm-123", "email": "owner@example.com", "role": "owner", "type": "user"}
    ]
    assert fake.calls == [
        ("get_spreadsheet", "sheet1"),
        ("list_permissions", "sheet1"),
    ]


@pytest.mark.parametrize(
    ("file_format", "mime"),
    [("csv", "text/csv"), ("xlsx", XLSX_MIME), ("pdf", "application/pdf")],
)
def test_export_spreadsheet_maps_format_to_mime(fake, file_format, mime):
    result = sheets.export_spreadsheet(None, None, "sheet1", file_format)
    assert result["format"] == file_format
    assert result["mime"] == mime
    assert result["filename"] == f"spreadsheet.{file_format}"
    assert base64.b64decode(result["content_base64"]) == b"%PDF-1.4 fake export"
    assert fake.calls[-1] == ("export_file", "sheet1", mime)


def test_export_spreadsheet_rejects_unknown_format(fake):
    with pytest.raises(ToolApiError) as exc:
        sheets.export_spreadsheet(None, None, "sheet1", "docx")
    assert exc.value.code == "invalid_request"
    assert fake.calls == []


def test_export_spreadsheet_not_found(fake):
    with pytest.raises(ToolApiError) as exc:
        sheets.export_spreadsheet(None, None, "missing", "pdf")
    assert (exc.value.code, exc.value.status_code) == ("spreadsheet_not_found", 404)


# ---- error wrapping ----


def test_raw_google_error_is_wrapped_as_google_api_error(fake, monkeypatch):
    def fail(*args):
        raise HttpError(SimpleNamespace(status=500, reason="Backend Error"), b"{}")

    monkeypatch.setattr(fake, "rename_file", fail)
    with pytest.raises(ToolApiError) as exc:
        sheets.rename_spreadsheet(None, None, "sheet1", "New")
    assert (exc.value.code, exc.value.status_code) == ("google_api_error", 502)
    assert "500" in exc.value.message


def test_permission_error_maps_to_403(fake, monkeypatch):
    def fail(*args):
        raise HttpError(SimpleNamespace(status=403, reason="Forbidden"), b"{}")

    monkeypatch.setattr(fake, "trash_document", fail)
    with pytest.raises(ToolApiError) as exc:
        sheets.trash_spreadsheet(None, None, "sheet1", True)
    assert exc.value.code == "google_api_error"
    assert exc.value.status_code == 403
