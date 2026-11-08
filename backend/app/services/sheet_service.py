"""Spreadsheet validation and data operations for the agent tool API."""

import csv
from functools import wraps
from io import StringIO
from typing import NamedTuple

from sqlalchemy.orm import Session

from app.core import tool_errors
from app.document_engine.a1 import A1Range, InvalidRangeError, col_letter, format_a1, parse_a1
from app.document_engine.providers.google_sheets import (
    GoogleSheetsProvider,
    SheetApiError,
    SpreadsheetNotFound,
)
from app.services import google_oauth_service

READ_CELL_CAP = 200_000
WRITE_CELL_CAP = 50_000
CELL_TEXT_MAX = 50_000


class SheetTab(NamedTuple):
    name: str
    sheet_id: int
    row_count: int
    column_count: int
    hidden: bool


def _provider(db: Session, tool_ctx) -> GoogleSheetsProvider:
    try:
        credentials = google_oauth_service.get_credentials(db, tool_ctx.connection)
    except google_oauth_service.GoogleReconnectRequired:
        raise tool_errors.google_reconnect_required() from None
    return GoogleSheetsProvider(credentials)


def _tabs(meta: dict) -> list[SheetTab]:
    tabs = []
    for sheet in meta.get("sheets", []):
        properties = sheet["properties"]
        grid = properties.get("gridProperties", {})
        tabs.append(
            SheetTab(
                properties["title"],
                properties["sheetId"],
                grid.get("rowCount", 1000),
                grid.get("columnCount", 26),
                properties.get("hidden", False),
            )
        )
    return tabs


def _resolve_sheet(tabs: list[SheetTab], sheet: str | None) -> SheetTab:
    for tab in tabs:
        if (sheet is None and not tab.hidden) or (sheet is not None and tab.name == sheet):
            return tab
    if sheet is None:
        raise tool_errors.invalid_request("spreadsheet has no visible tabs")
    raise tool_errors.sheet_not_found(sheet, [tab.name for tab in tabs])


def _check_cells(rows, confirm: bool) -> int:
    if not isinstance(rows, list) or not rows or any(not isinstance(row, list) for row in rows):
        raise tool_errors.invalid_request("rows must be a non-empty 2D array")
    total = 0
    for i, row in enumerate(rows):
        total += len(row)
        for j, cell in enumerate(row):
            if cell is not None and not isinstance(cell, (str, int, float, bool)):
                raise tool_errors.invalid_request(
                    f"row {i}, cell {j}: unsupported value type {type(cell).__name__}; "
                    "use string, number, boolean, or null"
                )
            if isinstance(cell, str) and len(cell) > CELL_TEXT_MAX:
                raise tool_errors.invalid_request(
                    f"row {i}, cell {j}: text exceeds {CELL_TEXT_MAX} characters"
                )
    if total > WRITE_CELL_CAP and not confirm:
        raise tool_errors.confirmation_required({"total_cells": total, "cap": WRITE_CELL_CAP})
    return total


def _range_cells(r: A1Range) -> int:
    if r.end_row == 0 or r.end_col == 0:
        return 0
    return (r.end_row - r.start_row + 1) * (r.end_col - r.start_col + 1)


def _check_read_size(r: A1Range, tab: SheetTab) -> None:
    end_row = r.end_row or tab.row_count
    end_col = r.end_col or tab.column_count
    cells = max(0, end_row - r.start_row + 1) * max(0, end_col - r.start_col + 1)
    if cells > READ_CELL_CAP:
        raise tool_errors.sheet_too_large(cells)


def _sheet_errors(fn):
    @wraps(fn)
    def wrapped(db, tool_ctx, spreadsheet_id, *args, **kwargs):
        try:
            return fn(db, tool_ctx, spreadsheet_id, *args, **kwargs)
        except SpreadsheetNotFound:
            raise tool_errors.spreadsheet_not_found(spreadsheet_id) from None
        except SheetApiError as exc:
            raise tool_errors.ToolApiError("google_api_error", 502, str(exc)) from None

    return wrapped


def _parse_range(range_str: str) -> A1Range:
    try:
        return parse_a1(range_str)
    except InvalidRangeError as exc:
        raise tool_errors.invalid_request(str(exc)) from None


def _check_range_sheet(r: A1Range, sheet: str | None) -> None:
    if sheet is not None and r.sheet is not None and r.sheet != sheet:
        raise tool_errors.invalid_request(
            f"range sheet prefix '{r.sheet}' does not match sheet '{sheet}'"
        )


def _qualified_range(r: A1Range, tab: SheetTab) -> str:
    _check_range_sheet(r, tab.name)
    return format_a1(r._replace(sheet=tab.name))


def _whole_tab_size(tab: SheetTab) -> None:
    _check_read_size(A1Range(None, 1, 1, 0, 0), tab)


def _markdown_table(values: list[list]) -> str:
    if not values:
        return ""
    width = max(map(len, values))

    def row(cells):
        cells = list(cells) + [None] * (width - len(cells))
        return (
            "| "
            + " | ".join("" if cell is None else str(cell).replace("|", "\\|") for cell in cells)
            + " |"
        )

    return "\n".join([row(values[0]), row(["---"] * width), *map(row, values[1:])])


@_sheet_errors
def read_values(
    db, tool_ctx, spreadsheet_id, sheet=None, range_str=None, mode="values", fmt="json"
) -> dict:
    if mode not in {"values", "formulas", "both"}:
        raise tool_errors.invalid_request("mode must be 'values', 'formulas', or 'both'")
    if fmt not in {"json", "csv", "markdown"}:
        raise tool_errors.invalid_request("format must be 'json', 'csv', or 'markdown'")
    parsed = _parse_range(range_str) if range_str is not None else None
    if parsed is not None:
        _check_range_sheet(parsed, sheet)
    provider = _provider(db, tool_ctx)
    meta = provider.get_spreadsheet(spreadsheet_id)
    tab = _resolve_sheet(_tabs(meta), sheet)
    if parsed is None:
        _whole_tab_size(tab)
        range_a1 = tab.name
    else:
        range_a1 = _qualified_range(parsed, tab)
        if (parsed.end_row and parsed.end_row > tab.row_count) or (
            parsed.end_col and parsed.end_col > tab.column_count
        ):
            raise tool_errors.invalid_request(
                f"range {range_str} exceeds bounds (max column {col_letter(tab.column_count)}, "
                f"max row {tab.row_count})"
            )
        _check_read_size(parsed, tab)
    formulas = None
    if mode == "values":
        values = provider.get_values(spreadsheet_id, range_a1)
    else:
        data = provider.get_values_with_formulas(spreadsheet_id, range_a1)
        values, formulas = data["values"], data["formulas"]
    csv_text = None
    markdown = None
    if fmt == "csv":
        output = StringIO(newline="")
        csv.writer(output).writerows(values)
        csv_text = output.getvalue()
    elif fmt == "markdown":
        markdown = _markdown_table(values)
    return {
        "spreadsheet_id": spreadsheet_id,
        "title": meta.get("properties", {}).get("title", ""),
        "sheet": tab.name,
        "range": range_a1,
        "mode": mode,
        "format": fmt,
        "values": values,
        "formulas": formulas,
        "csv": csv_text,
        "markdown": markdown,
    }


@_sheet_errors
def write_values(
    db, tool_ctx, spreadsheet_id, sheet, range_str, rows, mode="user_entered", confirm=False
) -> dict:
    _check_cells(rows, confirm)
    if mode not in {"user_entered", "raw"}:
        raise tool_errors.invalid_request("mode must be 'user_entered' or 'raw'")
    parsed = _parse_range(range_str)
    _check_range_sheet(parsed, sheet)
    if not parsed.end_row or not parsed.end_col:
        raise tool_errors.invalid_request(
            "writes require an explicit cell range like A1:D10 or a single cell"
        )
    provider = _provider(db, tool_ctx)
    tab = _resolve_sheet(_tabs(provider.get_spreadsheet(spreadsheet_id)), sheet)
    range_a1 = _qualified_range(parsed, tab)
    if parsed.end_row > tab.row_count or parsed.end_col > tab.column_count:
        provider.ensure_grid(
            spreadsheet_id,
            tab.sheet_id,
            max(tab.row_count, parsed.end_row),
            max(tab.column_count, parsed.end_col),
        )
    updated = provider.update_values(spreadsheet_id, range_a1, rows, mode)
    return {
        "spreadsheet_id": spreadsheet_id,
        "sheet": tab.name,
        "range": range_a1,
        "updated_cells": updated,
        "summary": f"Updated {updated} cells in {range_a1}",
    }


@_sheet_errors
def append_values(db, tool_ctx, spreadsheet_id, sheet, rows, confirm=False) -> dict:
    _check_cells(rows, confirm)
    provider = _provider(db, tool_ctx)
    tab = _resolve_sheet(_tabs(provider.get_spreadsheet(spreadsheet_id)), sheet)
    range_a1 = provider.append_values(spreadsheet_id, tab.name, rows)
    return {
        "spreadsheet_id": spreadsheet_id,
        "sheet": tab.name,
        "range": range_a1,
        "updated_rows": len(rows),
        "summary": f"Appended {len(rows)} rows to {tab.name}",
    }


@_sheet_errors
def clear_values(db, tool_ctx, spreadsheet_id, sheet, range_str, confirm=False) -> dict:
    parsed = _parse_range(range_str)
    _check_range_sheet(parsed, sheet)
    provider = _provider(db, tool_ctx)
    tab = _resolve_sheet(_tabs(provider.get_spreadsheet(spreadsheet_id)), sheet)
    range_a1 = _qualified_range(parsed, tab)
    if not confirm:
        raise tool_errors.confirmation_required(
            {
                "sheet": tab.name,
                "range": range_a1,
                "estimated_cells": _range_cells(parsed) or "used range",
            }
        )
    provider.clear_values(spreadsheet_id, range_a1)
    return {
        "spreadsheet_id": spreadsheet_id,
        "sheet": tab.name,
        "range": range_a1,
        "summary": f"Cleared {range_a1}",
    }


@_sheet_errors
def find_replace(
    db,
    tool_ctx,
    spreadsheet_id,
    find,
    replace="",
    sheet=None,
    match_case=False,
    whole_cell=False,
    confirm=False,
) -> dict:
    if not isinstance(find, str) or not find:
        raise tool_errors.invalid_request("find must be a non-empty string")
    if not isinstance(replace, str):
        raise tool_errors.invalid_request("replace must be a string")
    provider = _provider(db, tool_ctx)
    tabs = _tabs(provider.get_spreadsheet(spreadsheet_id))
    if sheet is None:
        _resolve_sheet(tabs, None)
    targets = [_resolve_sheet(tabs, sheet)] if sheet is not None else tabs
    if not confirm:
        for tab in targets:
            _whole_tab_size(tab)
        needle = find if match_case else find.lower()
        occurrences = 0
        for tab in targets:
            for row in provider.get_values(spreadsheet_id, tab.name):
                for cell in row:
                    value = "" if cell is None else str(cell)
                    value = value if match_case else value.lower()
                    matches = value == needle if whole_cell else needle in value
                    if matches:
                        occurrences += 1
        raise tool_errors.confirmation_required(
            {"occurrences": occurrences, "sheet": sheet or "all tabs"}
        )
    changed = provider.find_replace(spreadsheet_id, sheet, find, replace, match_case, whole_cell)
    return {
        "spreadsheet_id": spreadsheet_id,
        "occurrences_changed": changed,
        "summary": f"Changed {changed} occurrences",
    }
