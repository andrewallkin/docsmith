"""Service tests using an in-memory stand-in for the external Sheets API."""

import pytest

from app.core.tool_errors import ToolApiError
from app.document_engine.providers.google_sheets import SpreadsheetNotFound
from app.services import sheet_service as sheets


class FakeSheetsProvider:
    def __init__(self):
        self.meta = {
            "spreadsheetId": "sheet1",
            "properties": {"title": "Test spreadsheet"},
            "sheets": [
                {
                    "properties": {
                        "title": "Sheet1",
                        "sheetId": 0,
                        "gridProperties": {"rowCount": 100, "columnCount": 26},
                    }
                },
                {
                    "properties": {
                        "title": "Data",
                        "sheetId": 1,
                        "gridProperties": {"rowCount": 50, "columnCount": 10},
                    }
                },
            ],
        }
        self.data = {}
        self.calls = []

    def get_spreadsheet(self, spreadsheet_id):
        self.calls.append(("get_spreadsheet", spreadsheet_id))
        if spreadsheet_id != self.meta["spreadsheetId"]:
            raise SpreadsheetNotFound(spreadsheet_id)
        return self.meta

    def get_values(self, spreadsheet_id, range_a1):
        self.calls.append(("get_values", spreadsheet_id, range_a1))
        return self.data.get(range_a1, [])

    def get_values_with_formulas(self, spreadsheet_id, range_a1):
        self.calls.append(("get_values_with_formulas", spreadsheet_id, range_a1))
        return {
            "values": self.data.get(range_a1, []),
            "formulas": self.data.get(f"{range_a1}:formulas", []),
        }

    def update_values(self, spreadsheet_id, range_a1, rows, mode):
        self.calls.append(("update_values", spreadsheet_id, range_a1, rows, mode))
        self.data[range_a1] = rows
        return sum(map(len, rows))

    def append_values(self, spreadsheet_id, sheet, rows):
        self.calls.append(("append_values", spreadsheet_id, sheet, rows))
        return f"{sheet}!A2:B2"

    def clear_values(self, spreadsheet_id, range_a1):
        self.calls.append(("clear_values", spreadsheet_id, range_a1))
        self.data.pop(range_a1, None)

    def batch_update(self, spreadsheet_id, requests):
        self.calls.append(("batch_update", spreadsheet_id, requests))
        return {"replies": []}

    def ensure_grid(self, spreadsheet_id, sheet_id, rows, cols):
        self.calls.append(("ensure_grid", spreadsheet_id, sheet_id, rows, cols))

    def find_replace(self, spreadsheet_id, sheet_name, find, replace, match_case, whole_cell):
        self.calls.append(
            ("find_replace", spreadsheet_id, sheet_name, find, replace, match_case, whole_cell)
        )
        return 2

    def create_spreadsheet(self, title, sheet_titles):
        self.calls.append(("create_spreadsheet", title, sheet_titles))
        return self.meta


@pytest.fixture
def fake(monkeypatch):
    provider = FakeSheetsProvider()
    monkeypatch.setattr(sheets, "_provider", lambda db, tool_ctx: provider)
    return provider


@pytest.mark.parametrize("mode", ["user_entered", "raw"])
def test_write_user_entered_vs_raw(fake, mode):
    result = sheets.write_values(None, None, "sheet1", "Sheet1", "A1", [["=1+1"]], mode)
    assert result["updated_cells"] == 1
    assert fake.calls[-1][-1] == mode  # provider maps to USER_ENTERED / RAW


def test_write_grows_grid(fake):
    sheets.write_values(None, None, "sheet1", "Sheet1", "AA501", [[1]])
    assert fake.calls[-2] == ("ensure_grid", "sheet1", 0, 501, 27)
    assert fake.calls[-1][0] == "update_values"


def test_write_inverted_range_never_mutates(fake):
    with pytest.raises(ToolApiError, match="inverted"):
        sheets.write_values(None, None, "sheet1", "Sheet1", "B2:A1", [[1]])
    assert not any(call[0] in {"ensure_grid", "update_values"} for call in fake.calls)


@pytest.mark.parametrize("range_str", ["A:A", "1:2", "A1:B"])
def test_write_rejects_unbounded_range(fake, range_str):
    with pytest.raises(ToolApiError) as exc:
        sheets.write_values(None, None, "sheet1", "Sheet1", range_str, [[1]])
    assert exc.value.code == "invalid_request"
    assert not any(call[0] in {"ensure_grid", "update_values"} for call in fake.calls)


def test_read_out_of_bounds_names_grid(fake):
    with pytest.raises(ToolApiError) as exc:
        sheets.read_values(None, None, "sheet1", "Sheet1", "AA1")
    assert "max column Z, max row 100" in exc.value.message
    assert not any(call[0] == "get_values" for call in fake.calls)


def test_write_unknown_sheet_lists_available(fake):
    with pytest.raises(ToolApiError) as exc:
        sheets.write_values(None, None, "sheet1", "Missing", "A1", [[1]])
    assert exc.value.code == "sheet_not_found"
    assert "Sheet1" in exc.value.message and "Data" in exc.value.message


@pytest.mark.parametrize("mode", ["formulas", "both"])
def test_read_formulas_and_both(fake, mode):
    fake.data["'Sheet1'!A1"] = [[2]]
    fake.data["'Sheet1'!A1:formulas"] = [["=1+1"]]
    result = sheets.read_values(None, None, "sheet1", "Sheet1", "A1", mode)
    assert result["values"] == [[2]]
    assert result["formulas"] == [["=1+1"]]
    assert fake.calls[-1][0] == "get_values_with_formulas"


def test_read_csv_and_markdown(fake):
    fake.data["Sheet1"] = [["Name", "Value"], ["a|b", None], ["x,y", 5]]
    csv_result = sheets.read_values(None, None, "sheet1", fmt="csv")
    markdown_result = sheets.read_values(None, None, "sheet1", fmt="markdown")
    assert csv_result["csv"] == 'Name,Value\r\na|b,\r\n"x,y",5\r\n'
    assert markdown_result["markdown"] == (
        "| Name | Value |\n| --- | --- |\n| a\\|b |  |\n| x,y | 5 |"
    )


def test_read_over_cap_errors_with_paging_hint(fake):
    fake.meta["sheets"][0]["properties"]["gridProperties"]["rowCount"] = 10000
    with pytest.raises(ToolApiError) as exc:
        sheets.read_values(None, None, "sheet1")
    assert exc.value.code == "sheet_too_large"
    assert "range" in exc.value.message
    assert not any(call[0] == "get_values" for call in fake.calls)


def test_clear_requires_confirm_with_preview(fake):
    with pytest.raises(ToolApiError) as exc:
        sheets.clear_values(None, None, "sheet1", "Data", "A1:B3")
    assert (exc.value.code, exc.value.status_code) == ("confirmation_required", 422)
    assert exc.value.extra["preview"] == {
        "sheet": "Data",
        "range": "Data!A1:B3",
        "estimated_cells": 6,
    }
    assert not any(call[0] == "clear_values" for call in fake.calls)


def test_clear_confirmed(fake):
    result = sheets.clear_values(None, None, "sheet1", "Data", "A1:B3", True)
    assert result["summary"].startswith("Cleared")
    assert fake.calls[-1] == ("clear_values", "sheet1", "Data!A1:B3")


def test_find_replace_requires_confirm_counts_matches(fake):
    fake.data["Data"] = [["Alpha", "alpha alpha", "beta"], [3, "ALPHA"]]
    with pytest.raises(ToolApiError) as exc:
        sheets.find_replace(None, None, "sheet1", "alpha", sheet="Data")
    assert exc.value.extra["preview"] == {"occurrences": 3, "sheet": "Data"}
    assert not any(call[0] == "find_replace" for call in fake.calls)


def test_find_replace_all_tabs_when_sheet_omitted(fake):
    fake.data["Sheet1"] = [["x", "X"]]
    fake.data["Data"] = [["X", "xx"]]
    with pytest.raises(ToolApiError) as exc:
        sheets.find_replace(None, None, "sheet1", "X", match_case=True, whole_cell=True)
    assert exc.value.extra["preview"] == {"occurrences": 2, "sheet": "all tabs"}
    result = sheets.find_replace(None, None, "sheet1", "X", "Y", confirm=True)
    assert result["occurrences_changed"] == 2
    assert fake.calls[-1][2] is None


def test_append_returns_updated_range(fake):
    result = sheets.append_values(None, None, "sheet1", "Data", [[1, 2]])
    assert result["range"] == "Data!A2:B2"
    assert result["updated_rows"] == 1


def test_missing_spreadsheet_maps_to_tool_error(fake):
    with pytest.raises(ToolApiError) as exc:
        sheets.read_values(None, None, "missing")
    assert exc.value.code == "spreadsheet_not_found"


def test_no_visible_tab_is_invalid_request(fake):
    for tab in fake.meta["sheets"]:
        tab["properties"]["hidden"] = True
    with pytest.raises(ToolApiError) as exc:
        sheets.read_values(None, None, "sheet1")
    assert exc.value.code == "invalid_request"
    assert exc.value.message == "spreadsheet has no visible tabs"


@pytest.mark.parametrize("confirm", [False, True])
def test_find_replace_all_tabs_requires_at_least_one_visible_tab(fake, confirm):
    for tab in fake.meta["sheets"]:
        tab["properties"]["hidden"] = True
    with pytest.raises(ToolApiError) as exc:
        sheets.find_replace(None, None, "sheet1", "find", confirm=confirm)
    assert exc.value.code == "invalid_request"
    assert exc.value.message == "spreadsheet has no visible tabs"
    assert not any(call[0] == "find_replace" for call in fake.calls)


def test_object_sheet_metadata_does_not_crash(fake):
    fake.meta["sheets"].append(
        {"properties": {"title": "Chart", "sheetId": 2, "sheetType": "OBJECT"}}
    )
    assert sheets.read_values(None, None, "sheet1")["sheet"] == "Sheet1"


@pytest.mark.parametrize("operation", ["read", "write", "clear"])
def test_range_sheet_prefix_mismatch_is_rejected(fake, operation):
    with pytest.raises(ToolApiError) as exc:
        if operation == "read":
            sheets.read_values(None, None, "sheet1", "Data", "Sheet1!A1")
        elif operation == "write":
            sheets.write_values(None, None, "sheet1", "Data", "Sheet1!A1", [[1]])
        else:
            sheets.clear_values(None, None, "sheet1", "Data", "Sheet1!A1", True)
    assert exc.value.code == "invalid_request"
    assert "does not match" in exc.value.message
    assert not any(
        call[0] in {"get_values", "update_values", "clear_values", "ensure_grid"}
        for call in fake.calls
    )


@pytest.mark.parametrize("operation", ["read", "write", "clear"])
def test_known_sheet_prefix_mismatch_fails_before_google_call(fake, operation):
    with pytest.raises(ToolApiError):
        if operation == "read":
            sheets.read_values(None, None, "sheet1", "Data", "Sheet1!A1")
        elif operation == "write":
            sheets.write_values(None, None, "sheet1", "Data", "Sheet1!A1", [[1]])
        else:
            sheets.clear_values(None, None, "sheet1", "Data", "Sheet1!A1")
    assert fake.calls == []


def test_unconfirmed_find_replace_checks_all_target_sizes_before_reading(fake):
    fake.meta["sheets"][1]["properties"]["gridProperties"]["rowCount"] = 30000
    with pytest.raises(ToolApiError) as exc:
        sheets.find_replace(None, None, "sheet1", "search")
    assert exc.value.code == "sheet_too_large"
    assert not any(call[0] == "get_values" for call in fake.calls)


@pytest.mark.parametrize("mode,fmt", [("nope", "json"), ("values", "nope")])
def test_invalid_read_options_rejected_before_google_call(fake, mode, fmt):
    with pytest.raises(ToolApiError) as exc:
        sheets.read_values(None, None, "sheet1", mode=mode, fmt=fmt)
    assert exc.value.code == "invalid_request"
    assert fake.calls == []


@pytest.mark.parametrize("range_str", ["A0", "B3:A2"])
def test_invalid_write_range_rejected_before_google_call(fake, range_str):
    with pytest.raises(ToolApiError) as exc:
        sheets.write_values(None, None, "sheet1", "Data", range_str, [[1]])
    assert exc.value.code == "invalid_request"
    assert fake.calls == []


def test_invalid_write_rows_rejected_before_google_call(fake):
    with pytest.raises(ToolApiError) as exc:
        sheets.write_values(None, None, "sheet1", "Data", "A1", [[{}]])
    assert exc.value.code == "invalid_request"
    assert fake.calls == []


def test_clear_unbounded_range_previews_used_range(fake):
    with pytest.raises(ToolApiError) as exc:
        sheets.clear_values(None, None, "sheet1", "Data", "A:B")
    assert exc.value.extra["preview"]["estimated_cells"] == "used range"
    assert not any(call[0] == "clear_values" for call in fake.calls)


def test_write_rejects_invalid_mode_before_google_call(fake):
    with pytest.raises(ToolApiError) as exc:
        sheets.write_values(None, None, "sheet1", "Data", "A1", [[1]], mode="bad")
    assert exc.value.code == "invalid_request"
    assert fake.calls == []


def test_append_over_cap_requires_confirmation_before_google_call(fake):
    with pytest.raises(ToolApiError) as exc:
        sheets.append_values(None, None, "sheet1", "Data", [[0] * 50001])
    assert exc.value.extra["preview"] == {"total_cells": 50001, "cap": 50000}
    assert fake.calls == []


def test_find_replace_rejects_empty_search_before_google_call(fake):
    with pytest.raises(ToolApiError) as exc:
        sheets.find_replace(None, None, "sheet1", "", confirm=True)
    assert exc.value.code == "invalid_request"
    assert fake.calls == []


def test_read_translates_not_found_during_values_fetch(fake, monkeypatch):
    def missing(spreadsheet_id, range_a1):
        raise SpreadsheetNotFound(spreadsheet_id)

    monkeypatch.setattr(fake, "get_values", missing)
    with pytest.raises(ToolApiError) as exc:
        sheets.read_values(None, None, "sheet1")
    assert exc.value.code == "spreadsheet_not_found"
