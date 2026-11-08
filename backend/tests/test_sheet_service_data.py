"""Service tests using an in-memory stand-in for the external Sheets API."""

import re
from copy import deepcopy

import pytest

from app.core.tool_errors import ToolApiError
from app.document_engine.a1 import InvalidRangeError, parse_a1, quote_sheet_name
from app.document_engine.providers.google_drive_base import DocNotFound
from app.document_engine.providers.google_sheets import SheetApiError, SpreadsheetNotFound
from app.services import sheet_service as sheets

SHEET_URL = "https://docs.google.com/spreadsheets/d/sheet1/edit"


class FakeSheetsProvider:
    """Store values under the sent range_a1; formulas use a ':formulas' suffix."""

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
        self.file_name = "Test spreadsheet"
        self.modified_time = "2026-11-01T00:00:00Z"
        self.trashed = False

    def get_spreadsheet(self, spreadsheet_id):
        self.calls.append(("get_spreadsheet", spreadsheet_id))
        if spreadsheet_id != self.meta["spreadsheetId"]:
            raise SpreadsheetNotFound(spreadsheet_id)
        return self.meta

    def get_values(self, spreadsheet_id, range_a1):
        self.calls.append(("get_values", spreadsheet_id, range_a1))
        return deepcopy(self.data.get(range_a1, []))

    def get_values_with_formulas(self, spreadsheet_id, range_a1):
        self.calls.append(("get_values_with_formulas", spreadsheet_id, range_a1))
        return {
            "values": deepcopy(self.data.get(range_a1, [])),
            "formulas": deepcopy(self.data.get(f"{range_a1}:formulas", [])),
        }

    def update_values(self, spreadsheet_id, range_a1, rows, mode):
        self.calls.append(("update_values", spreadsheet_id, range_a1, rows, mode))
        parsed = parse_a1(range_a1)
        tab = self._sheet(parsed.sheet)
        grid = tab["gridProperties"]
        end_row = max(parsed.end_row, parsed.start_row + len(rows) - 1)
        end_col = max(parsed.end_col, parsed.start_col + max(map(len, rows), default=0) - 1)
        if end_row > grid["rowCount"] or end_col > grid["columnCount"]:
            raise SheetApiError(f"range {range_a1} exceeds grid")
        self.data[range_a1] = deepcopy(rows)
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
        for sheet in self.meta["sheets"]:
            props = sheet["properties"]
            if props["sheetId"] == sheet_id:
                props["gridProperties"] = {"rowCount": rows, "columnCount": cols}
                return
        raise SheetApiError(f"Sheet ID not found: {sheet_id}")

    def _sheet(self, name):
        for sheet in self.meta["sheets"]:
            if sheet["properties"]["title"] == name:
                return sheet["properties"]
        raise SheetApiError(f"Sheet not found: {name}")

    def find_replace(self, spreadsheet_id, sheet_name, find, replace, match_case, whole_cell):
        self.calls.append(
            ("find_replace", spreadsheet_id, sheet_name, find, replace, match_case, whole_cell)
        )
        if sheet_name is not None:
            self._sheet(sheet_name)
        names = [s["properties"]["title"] for s in self.meta["sheets"]]
        pattern = re.compile(re.escape(find), 0 if match_case else re.IGNORECASE)
        count = 0
        for key, rows in self.data.items():
            if key.endswith(":formulas"):
                continue
            name = next((n for n in names if key in (n, quote_sheet_name(n))), None)
            if name is None:
                try:
                    name = parse_a1(key).sheet
                except InvalidRangeError:
                    continue
            if name not in names or (sheet_name is not None and name != sheet_name):
                continue
            for row in rows:
                for i, cell in enumerate(row):
                    if cell is None:
                        continue
                    value = str(cell)
                    if whole_cell:
                        if pattern.fullmatch(value):
                            row[i] = replace
                            count += 1
                    else:
                        result, changed = pattern.subn(lambda _: replace, value)
                        if changed:
                            row[i] = result
                            count += changed
        return count

    def create_spreadsheet(self, title, sheet_titles):
        self.calls.append(("create_spreadsheet", title, sheet_titles))
        names = list(sheet_titles) or ["Sheet1"]
        return {
            "spreadsheetId": "sheet1",
            "spreadsheetUrl": SHEET_URL,
            "properties": {"title": title},
            "sheets": [
                {
                    "properties": {
                        "title": name,
                        "sheetId": index,
                        "gridProperties": {"rowCount": 1000, "columnCount": 26},
                    }
                }
                for index, name in enumerate(names)
            ],
        }

    # ---- Drive file-level operations (Task 6) ----

    def _file_or_404(self, file_id):
        if file_id != self.meta["spreadsheetId"]:
            raise DocNotFound(f"File not found: {file_id}")

    def get_file_metadata(self, file_id):
        self.calls.append(("get_file_metadata", file_id))
        self._file_or_404(file_id)
        return {"id": "sheet1", "name": self.file_name, "modified_time": self.modified_time}

    def search_spreadsheets(self, query, limit, folder_id=None):
        self.calls.append(("search_spreadsheets", query, limit, folder_id))
        return [
            {
                "id": "sheet1",
                "name": self.file_name,
                "url": SHEET_URL,
                "modified_time": self.modified_time,
            }
        ]

    def rename_file(self, file_id, title):
        self.calls.append(("rename_file", file_id, title))
        self._file_or_404(file_id)
        self.file_name = title

    def trash_document(self, file_id):
        self.calls.append(("trash_document", file_id))
        self._file_or_404(file_id)
        self.trashed = True

    def untrash_document(self, file_id):
        self.calls.append(("untrash_document", file_id))
        self._file_or_404(file_id)
        self.trashed = False

    def move_file(self, file_id, add_parent_id, remove_current=True):
        self.calls.append(("move_file", file_id, add_parent_id, remove_current))
        self._file_or_404(file_id)
        if add_parent_id != "folder1":
            raise DocNotFound(f"Folder not found: {add_parent_id}")

    def copy_spreadsheet(self, file_id, new_title=None):
        self.calls.append(("copy_spreadsheet", file_id, new_title))
        self._file_or_404(file_id)
        return {
            "id": "sheet1-copy",
            "name": new_title or "Test spreadsheet",
            "url": "https://docs.google.com/spreadsheets/d/sheet1-copy/edit",
        }

    def share_file(self, file_id, email, role, notify=True):
        self.calls.append(("share_file", file_id, email, role, notify))
        self._file_or_404(file_id)
        return {"id": "perm-123", "email": email, "role": role}

    def list_permissions(self, file_id):
        self.calls.append(("list_permissions", file_id))
        self._file_or_404(file_id)
        return [{"id": "perm-123", "email": "owner@example.com", "role": "owner", "type": "user"}]

    def export_file(self, file_id, mime):
        self.calls.append(("export_file", file_id, mime))
        self._file_or_404(file_id)
        return b"%PDF-1.4 fake export"


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
    fake.data["'Sheet1'"] = [["Name", "Value"], ["a|b", None], ["x,y", 5]]
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
    fake.data["'Sheet1'"] = [["x", "X"]]
    fake.data["Data"] = [["X", "xx"]]
    with pytest.raises(ToolApiError) as exc:
        sheets.find_replace(None, None, "sheet1", "X", match_case=True, whole_cell=True)
    assert exc.value.extra["preview"] == {"occurrences": 2, "sheet": "all tabs"}
    result = sheets.find_replace(None, None, "sheet1", "X", "Y", confirm=True)
    assert result["occurrences_changed"] == 5
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


def test_unconfirmed_find_replace_scans_large_and_small_targets(fake):
    fake.meta["sheets"][1]["properties"]["gridProperties"]["rowCount"] = 30000
    with pytest.raises(ToolApiError) as exc:
        sheets.find_replace(None, None, "sheet1", "search")
    assert exc.value.code == "confirmation_required"
    assert [call[2] for call in fake.calls if call[0] == "get_values"] == [
        "'Sheet1'",
        "Data!A1:J20000",
        "Data!A20001:J30000",
    ]


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


def test_single_cell_write_grows_to_data_extent(fake):
    rows = [[1, 2, 3], [4, 5, 6]]
    result = sheets.write_values(None, None, "sheet1", "Sheet1", "AA501", rows)
    assert result["updated_cells"] == 6
    assert fake.calls[-2] == ("ensure_grid", "sheet1", 0, 502, 29)
    assert fake.meta["sheets"][0]["properties"]["gridProperties"] == {
        "rowCount": 502,
        "columnCount": 29,
    }


@pytest.mark.parametrize("rows", [[[1, 2], [3, 4], [5, 6]], [[1, 2, 3]]])
def test_multi_cell_write_rejects_overflow_before_metadata(fake, rows):
    with pytest.raises(ToolApiError) as exc:
        sheets.write_values(None, None, "sheet1", "Sheet1", "A1:B2", rows)
    assert exc.value.code == "invalid_request"
    assert "A1:B2" in exc.value.message and "extent" in exc.value.message
    assert fake.calls == []


def test_fake_enforces_current_grid_and_returns_copies():
    provider = FakeSheetsProvider()
    with pytest.raises(SheetApiError):
        provider.update_values("sheet1", "'Sheet1'!AA101", [[1]], "raw")
    provider.ensure_grid("sheet1", 0, 101, 27)
    provider.update_values("sheet1", "'Sheet1'!AA101", [[1]], "raw")
    result = provider.get_values("sheet1", "'Sheet1'!AA101")
    result[0][0] = 2
    assert provider.get_values("sheet1", "'Sheet1'!AA101") == [[1]]


def test_fake_find_replace_respects_sheet_and_options():
    provider = FakeSheetsProvider()
    provider.data["Data"] = [["Alpha alpha", "alpha", "ALPHA"]]
    provider.data["Sheet1"] = [["alpha"]]
    assert provider.find_replace("sheet1", "Data", "alpha", "X", True, True) == 1
    assert provider.data["Data"] == [["Alpha alpha", "X", "ALPHA"]]
    assert provider.data["Sheet1"] == [["alpha"]]
    assert provider.find_replace("sheet1", "Data", "alpha", "Y", False, False) == 3
    assert provider.data["Data"] == [["Y Y", "X", "Y"]]
    with pytest.raises(SheetApiError):
        provider.find_replace("sheet1", "Missing", "x", "y", False, False)


def test_find_replace_preview_chunks_large_grid(fake):
    fake.meta["sheets"][1]["properties"]["gridProperties"]["rowCount"] = 40001
    fake.data["Data!A1:J20000"] = [["find", "no"]]
    fake.data["Data!A20001:J40000"] = [["FIND"]]
    fake.data["Data!A40001:J40001"] = [["find me"]]
    with pytest.raises(ToolApiError) as exc:
        sheets.find_replace(None, None, "sheet1", "find", sheet="Data")
    assert exc.value.code == "confirmation_required"
    assert exc.value.extra["preview"]["occurrences"] == 3
    assert [c[2] for c in fake.calls if c[0] == "get_values"] == [
        "Data!A1:J20000",
        "Data!A20001:J40000",
        "Data!A40001:J40001",
    ]


def test_special_tab_whole_read_and_preview_use_quoted_name(fake):
    fake.meta["sheets"][1]["properties"]["title"] = "It's Data"
    fake.data["'It''s Data'"] = [["match"]]
    assert sheets.read_values(None, None, "sheet1", "It's Data")["values"] == [["match"]]
    with pytest.raises(ToolApiError) as exc:
        sheets.find_replace(None, None, "sheet1", "match", sheet="It's Data")
    assert exc.value.extra["preview"]["occurrences"] == 1
    assert fake.calls[-1] == ("get_values", "sheet1", "'It''s Data'")


@pytest.mark.parametrize("range_str", ["K1", "A51", "K:K", "51:51"])
@pytest.mark.parametrize("confirm", [False, True])
def test_clear_outside_grid_rejected_without_mutation(fake, range_str, confirm):
    with pytest.raises(ToolApiError) as exc:
        sheets.clear_values(None, None, "sheet1", "Data", range_str, confirm)
    assert exc.value.code == "invalid_request"
    assert "max column J, max row 50" in exc.value.message
    assert not any(call[0] == "clear_values" for call in fake.calls)


@pytest.mark.parametrize("operation", ["write", "append"])
def test_empty_rows_are_rejected_before_google_calls(fake, operation):
    with pytest.raises(ToolApiError) as exc:
        if operation == "write":
            sheets.write_values(None, None, "sheet1", "Data", "A1", [[]])
        else:
            sheets.append_values(None, None, "sheet1", "Data", [[]])
    assert exc.value.code == "invalid_request"
    assert fake.calls == []


def test_service_translates_google_api_error(fake, monkeypatch):
    def fail(*args):
        raise SheetApiError("Google API failed")

    monkeypatch.setattr(fake, "get_spreadsheet", fail)
    with pytest.raises(ToolApiError) as exc:
        sheets.read_values(None, None, "sheet1")
    assert (exc.value.code, exc.value.status_code, exc.value.message) == (
        "google_api_error",
        502,
        "Google API failed",
    )
