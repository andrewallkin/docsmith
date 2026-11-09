from types import SimpleNamespace

import pytest

from app.core import tool_errors
from app.document_engine.a1 import A1Range, parse_a1
from app.document_engine.providers.google_sheets import GoogleSheetsProvider
from app.services import google_oauth_service
from app.services import sheet_service as sheets


def test_tabs_extract_metadata_and_default_hidden():
    meta = {
        "spreadsheetId": "abc",
        "sheets": [
            {"properties": {"title": "Hidden", "sheetId": 12, "hidden": True,
                            "gridProperties": {"rowCount": 200, "columnCount": 5}}},
            {"properties": {"title": "Data", "sheetId": 0,
                            "gridProperties": {"rowCount": 1000, "columnCount": 26}}},
        ],
    }
    assert sheets._tabs(meta) == [
        sheets.SheetTab("Hidden", 12, 200, 5, True),
        sheets.SheetTab("Data", 0, 1000, 26, False),
    ]


def test_resolve_default_skips_hidden_but_named_hidden_is_accessible():
    hidden = sheets.SheetTab("Hidden", 1, 10, 10, True)
    visible = sheets.SheetTab("Data", 2, 10, 10, False)
    assert sheets._resolve_sheet([hidden, visible], None) == visible
    assert sheets._resolve_sheet([hidden, visible], "Hidden") == hidden


def test_resolve_unknown_reports_all_names():
    tabs = [sheets.SheetTab("A", 1, 10, 10, False), sheets.SheetTab("B", 2, 10, 10, True)]
    with pytest.raises(tool_errors.ToolApiError) as exc:
        sheets._resolve_sheet(tabs, "Missing")
    assert (exc.value.code, exc.value.status_code) == ("sheet_not_found", 404)
    assert exc.value.message == "sheet 'Missing' not found; available: A, B"


@pytest.mark.parametrize("rows", [None, [], "abc", [1], [[1], "row"], ([],)])
def test_check_cells_rejects_invalid_shape(rows):
    with pytest.raises(tool_errors.ToolApiError) as exc:
        sheets._check_cells(rows, False)
    assert (exc.value.code, exc.value.status_code) == ("invalid_request", 422)
    assert exc.value.message == "rows must be a non-empty 2D array"


def test_check_cells_counts_ragged_rows_and_accepts_supported_values():
    assert sheets._check_cells([["text", 4, 1.5, True, None], [], [False]], False) == 6


@pytest.mark.parametrize("bad", [{"x": 1}, [1], (1,), object()])
def test_check_cells_identifies_unsupported_type_and_position(bad):
    with pytest.raises(tool_errors.ToolApiError) as exc:
        sheets._check_cells([[1], [2, bad]], False)
    assert exc.value.code == "invalid_request"
    assert "row 1, cell 1" in exc.value.message
    assert type(bad).__name__ in exc.value.message
    assert "string, number, boolean, or null" in exc.value.message


def test_check_cells_enforces_text_limit_even_when_confirmed():
    assert sheets._check_cells([["x" * 50_000]], False) == 1
    with pytest.raises(tool_errors.ToolApiError) as exc:
        sheets._check_cells([["x" * 50_001]], True)
    assert exc.value.code == "invalid_request"
    assert "row 0, cell 0" in exc.value.message
    assert "50000" in exc.value.message


def test_check_cells_requires_confirmation_only_above_cap():
    assert sheets._check_cells([[0] * 50_000], False) == 50_000
    with pytest.raises(tool_errors.ToolApiError) as exc:
        sheets._check_cells([[0] * 50_001], False)
    assert (exc.value.code, exc.value.status_code) == ("confirmation_required", 422)
    assert exc.value.extra["preview"] == {"total_cells": 50_001, "cap": 50_000}
    assert sheets._check_cells([[0] * 50_001], True) == 50_001


@pytest.mark.parametrize(
    ("text", "expected"), [("B2:D4", 9), ("A1", 1), ("B:D", 0), ("2:5", 0)]
)
def test_range_cells_counts_bounded_or_returns_zero_for_unbounded(text, expected):
    assert sheets._range_cells(parse_a1(text)) == expected


@pytest.mark.parametrize("text", ["A1:H25000", "A:A", "1:2"])
def test_check_read_size_allows_cap_and_smaller_ranges(text):
    sheets._check_read_size(parse_a1(text), sheets.SheetTab("Data", 0, 100_000, 100, False))


@pytest.mark.parametrize(
    ("text", "cells"), [("A1:I25000", 225_000), ("A:C", 300_000), ("1:2001", 200_100)]
)
def test_check_read_size_rejects_oversized_bounded_and_grid_resolved_ranges(text, cells):
    with pytest.raises(tool_errors.ToolApiError) as exc:
        sheets._check_read_size(parse_a1(text), sheets.SheetTab("Data", 0, 100_000, 100, False))
    assert (exc.value.code, exc.value.status_code) == ("sheet_too_large", 422)
    assert str(cells) in exc.value.message or f"{cells:,}" in exc.value.message
    assert "200,000" in exc.value.message
    assert "page reads with the range parameter" in exc.value.message


def test_check_read_size_clamps_negative_extent_for_unbounded_ranges():
    tab = sheets.SheetTab("Data", 0, 100, 10, False)
    sheets._check_read_size(A1Range(None, 1000, 1, 0, 1), tab)
    sheets._check_read_size(A1Range(None, 1, 1000, 1, 0), tab)


def test_spreadsheet_not_found_includes_id():
    error = tool_errors.spreadsheet_not_found("spreadsheet-123")
    assert (error.code, error.status_code) == ("spreadsheet_not_found", 404)
    assert "spreadsheet-123" in error.message
    assert "not found or not accessible" in error.message


def test_provider_uses_credentials_without_building_google_client(monkeypatch):
    credentials = object()
    connection = object()
    db = object()

    def get_credentials(actual_db, actual_connection):
        assert actual_db is db
        assert actual_connection is connection
        return credentials

    monkeypatch.setattr(google_oauth_service, "get_credentials", get_credentials)
    provider = sheets._provider(db, SimpleNamespace(connection=connection))
    assert isinstance(provider, GoogleSheetsProvider)
    assert provider._credentials is credentials


def test_provider_maps_reconnect_error(monkeypatch):
    def get_credentials(db, connection):
        raise google_oauth_service.GoogleReconnectRequired()

    monkeypatch.setattr(google_oauth_service, "get_credentials", get_credentials)
    ctx = SimpleNamespace(connection=object(), user=SimpleNamespace(id="user-1"))
    with pytest.raises(tool_errors.ToolApiError) as exc:
        sheets._provider(object(), ctx)
    assert (exc.value.code, exc.value.status_code) == ("google_reconnect_required", 403)
    assert "reauth_url" in exc.value.extra
    assert "accounts.google.com" in exc.value.extra["reauth_url"]
