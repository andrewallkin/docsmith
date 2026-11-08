"""Sheets provider API contracts, using a fake Google client instead of the network."""

from types import SimpleNamespace

import pytest
from googleapiclient.errors import HttpError

from app.core.tool_errors import ToolApiError
from app.document_engine.providers import google_sheets
from app.document_engine.providers.google_sheets import (
    GoogleSheetsProvider,
    SheetApiError,
    SpreadsheetNotFound,
)
from app.services import sheet_service


class StubSheets:
    def __init__(self):
        self.calls = []
        self.responses = []

    def spreadsheets(self):
        return self

    def values(self):
        return self

    def __getattr__(self, name):
        def request(**kwargs):
            self.calls.append((name, kwargs))
            return self

        return request

    def execute(self):
        response = self.responses.pop(0) if self.responses else {}
        if isinstance(response, Exception):
            raise response
        return response


def http_error(status, message="Google error"):
    return HttpError(SimpleNamespace(status=status, reason=message), message.encode())


@pytest.fixture
def provider(monkeypatch):
    client = StubSheets()
    builds = []

    def build(*args, **kwargs):
        builds.append((args, kwargs))
        return client

    monkeypatch.setattr(google_sheets, "build", build)
    credentials = object()
    return GoogleSheetsProvider(credentials), client, builds, credentials


def test_get_spreadsheet_and_cached_client(provider):
    sheets, client, builds, credentials = provider
    metadata = {
        "spreadsheetId": "id",
        "sheets": [
            {
                "properties": {
                    "title": "Tab",
                    "sheetId": 3,
                    "gridProperties": {"rowCount": 10, "columnCount": 5},
                    "hidden": False,
                }
            }
        ],
    }
    client.responses = [metadata, metadata]
    assert sheets.get_spreadsheet("id") == metadata
    assert sheets.get_spreadsheet("id") == metadata
    assert client.calls == [("get", {"spreadsheetId": "id"})] * 2
    assert builds == [(("sheets", "v4"), {"credentials": credentials, "cache_discovery": False})]


def test_get_spreadsheet_404_raises_not_found(provider):
    sheets, client, _, _ = provider
    client.responses = [http_error(404)]
    with pytest.raises(SpreadsheetNotFound, match="missing"):
        sheets.get_spreadsheet("missing")


def test_create_spreadsheet_passes_sheet_titles(provider):
    sheets, client, _, _ = provider
    result = {"spreadsheetId": "id", "spreadsheetUrl": "url", "sheets": []}
    client.responses = [result]
    assert sheets.create_spreadsheet("Budget", ["One", "Two"]) == result
    assert client.calls == [
        (
            "create",
            {
                "body": {
                    "properties": {"title": "Budget"},
                    "sheets": [{"properties": {"title": "One"}}, {"properties": {"title": "Two"}}],
                }
            },
        )
    ]


def test_get_values_defaults_to_empty_and_preserves_default_render(provider):
    sheets, client, _, _ = provider
    client.responses = [{}, {"values": [["42"]]}]
    assert sheets.get_values("id", "Tab!A1") == []
    assert sheets.get_values("id", "Tab!A1") == [["42"]]
    assert client.calls == [("get", {"spreadsheetId": "id", "range": "Tab!A1"})] * 2


def test_get_values_with_formulas_reads_both_render_options(provider):
    sheets, client, _, _ = provider
    client.responses = [{"values": [[42]]}, {"values": [["=SUM(A1:A2)"]]}]
    assert sheets.get_values_with_formulas("id", "Tab!A1") == {
        "values": [[42]],
        "formulas": [["=SUM(A1:A2)"]],
    }
    assert client.calls == [
        ("get", {"spreadsheetId": "id", "range": "Tab!A1", "valueRenderOption": option})
        for option in ("UNFORMATTED_VALUE", "FORMULA")
    ]


@pytest.mark.parametrize("mode,option", [("user_entered", "USER_ENTERED"), ("raw", "RAW")])
def test_update_values_mode_maps_to_input_option(provider, mode, option):
    sheets, client, _, _ = provider
    client.responses = [{"updatedCells": 2}]
    assert sheets.update_values("id", "Tab!A1:B1", [[1, 2]], mode) == 2
    assert client.calls == [
        (
            "update",
            {
                "spreadsheetId": "id",
                "range": "Tab!A1:B1",
                "valueInputOption": option,
                "body": {"values": [[1, 2]]},
            },
        )
    ]


def test_update_values_rejects_unknown_mode(provider):
    sheets, client, _, _ = provider
    with pytest.raises(SheetApiError):
        sheets.update_values("id", "A1", [[1]], "other")
    assert client.calls == []


def test_append_uses_insert_rows_and_returns_updated_range(provider):
    sheets, client, _, _ = provider
    client.responses = [{"updates": {"updatedRange": "Tab!A2:B2"}}]
    assert sheets.append_values("id", "Tab", [[1, 2]]) == "Tab!A2:B2"
    assert client.calls == [
        (
            "append",
            {
                "spreadsheetId": "id",
                "range": "Tab!A1",
                "insertDataOption": "INSERT_ROWS",
                "valueInputOption": "USER_ENTERED",
                "body": {"values": [[1, 2]]},
            },
        )
    ]


@pytest.mark.parametrize(
    ("sheet", "range_a1"),
    [("Tab", "Tab!A1"), ("My Data", "'My Data'!A1"), ("It's Data", "'It''s Data'!A1")],
)
def test_append_quotes_sheet_name(provider, sheet, range_a1):
    sheets, client, _, _ = provider
    client.responses = [{"updates": {"updatedRange": range_a1}}]
    sheets.append_values("id", sheet, [[1]])
    assert client.calls[0][1]["range"] == range_a1


def test_clear_values_sends_range(provider):
    sheets, client, _, _ = provider
    assert sheets.clear_values("id", "Tab!A1:B2") is None
    assert client.calls == [("clear", {"spreadsheetId": "id", "range": "Tab!A1:B2", "body": {}})]


def test_batch_update_returns_response(provider):
    sheets, client, _, _ = provider
    client.responses = [{"replies": [{}]}]
    assert sheets.batch_update("id", [{"deleteSheet": {"sheetId": 1}}]) == {"replies": [{}]}
    assert client.calls == [
        (
            "batchUpdate",
            {"spreadsheetId": "id", "body": {"requests": [{"deleteSheet": {"sheetId": 1}}]}},
        )
    ]


def test_ensure_grid_sends_update_sheet_properties(provider):
    sheets, client, _, _ = provider
    assert sheets.ensure_grid("id", 7, 100, 20) is None
    assert client.calls == [
        (
            "batchUpdate",
            {
                "spreadsheetId": "id",
                "body": {
                    "requests": [
                        {
                            "updateSheetProperties": {
                                "properties": {
                                    "sheetId": 7,
                                    "gridProperties": {"rowCount": 100, "columnCount": 20},
                                },
                                "fields": "gridProperties.rowCount,gridProperties.columnCount",
                            }
                        }
                    ]
                },
            },
        )
    ]


def test_find_replace_all_sheets_uses_all_sheets_flag(provider):
    sheets, client, _, _ = provider
    client.responses = [{"replies": [{"findReplace": {"occurrencesChanged": 3}}]}]
    assert sheets.find_replace("id", None, "a", "b", True, False) == 3
    assert client.calls == [
        (
            "batchUpdate",
            {
                "spreadsheetId": "id",
                "body": {
                    "requests": [
                        {
                            "findReplace": {
                                "find": "a",
                                "replacement": "b",
                                "matchCase": True,
                                "matchEntireCell": False,
                                "allSheets": True,
                            }
                        }
                    ]
                },
            },
        )
    ]


def test_find_replace_resolves_sheet_name(provider):
    sheets, client, _, _ = provider
    client.responses = [
        {"sheets": [{"properties": {"title": "Tab", "sheetId": 42}}]},
        {"replies": [{"findReplace": {"occurrencesChanged": 1}}]},
    ]
    assert sheets.find_replace("id", "Tab", "a", "b", False, True) == 1
    assert client.calls[1] == (
        "batchUpdate",
        {
            "spreadsheetId": "id",
            "body": {
                "requests": [
                    {
                        "findReplace": {
                            "find": "a",
                            "replacement": "b",
                            "matchCase": False,
                            "matchEntireCell": True,
                            "sheetId": 42,
                        }
                    }
                ]
            },
        },
    )


def test_find_replace_unknown_sheet_is_a_provider_error(provider):
    sheets, client, _, _ = provider
    client.responses = [{"sheets": []}]
    with pytest.raises(SheetApiError, match="Missing"):
        sheets.find_replace("id", "Missing", "a", "b", False, False)
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    "method,args",
    [
        ("get_spreadsheet", ("id",)),
        ("get_values", ("id", "A1")),
        ("get_values_with_formulas", ("id", "A1")),
        ("update_values", ("id", "A1", [[1]], "raw")),
        ("append_values", ("id", "Tab", [[1]])),
        ("clear_values", ("id", "A1")),
        ("batch_update", ("id", [])),
        ("create_spreadsheet", ("title", [])),
    ],
)
@pytest.mark.parametrize("status", [403, 400])
def test_google_errors_are_translated(provider, method, args, status):
    sheets, client, _, _ = provider
    client.responses = [http_error(status, "Bad request")]
    with pytest.raises(SheetApiError) as caught:
        getattr(sheets, method)(*args)
    if status == 403:
        assert "accessible" in str(caught.value) and "account" in str(caught.value)
    else:
        assert "400" in str(caught.value) and "Bad request" in str(caught.value)


@pytest.mark.parametrize(("google_status", "tool_status"), [(403, 403), (400, 502)])
def test_sheet_service_preserves_permission_error_and_maps_other_failures(
    provider, monkeypatch, google_status, tool_status
):
    sheets, client, _, _ = provider
    client.responses = [http_error(google_status, "Bad request")]
    monkeypatch.setattr(sheet_service, "_provider", lambda db, ctx: sheets)
    with pytest.raises(ToolApiError) as exc:
        sheet_service.read_values(None, None, "id")
    assert exc.value.code == "google_api_error"
    assert exc.value.status_code == tool_status
    assert str(google_status) in exc.value.message


@pytest.mark.parametrize(
    "method,args",
    [
        ("get_values", ("id", "A1")),
        ("get_values_with_formulas", ("id", "A1")),
    ],
)
def test_read_404_raises_spreadsheet_not_found(provider, method, args):
    sheets, client, _, _ = provider
    client.responses = [http_error(404)]
    with pytest.raises(SpreadsheetNotFound, match="id"):
        getattr(sheets, method)(*args)


def test_batch_update_retries_transient_error(provider, monkeypatch):
    sheets, client, _, _ = provider
    monkeypatch.setattr(
        "app.document_engine.providers.google_drive_base.time.sleep", lambda _: None
    )
    client.responses = [http_error(503), {"replies": []}]
    assert sheets.batch_update("id", []) == {"replies": []}
    assert len(client.calls) == 2
