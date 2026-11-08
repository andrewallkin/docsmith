"""Spreadsheet validation and data operations for the agent tool API."""

import base64
import csv
from functools import wraps
from io import StringIO
from typing import NamedTuple

from googleapiclient.errors import HttpError
from sqlalchemy.orm import Session

from app.core import tool_errors
from app.document_engine.a1 import (
    A1Range,
    InvalidRangeError,
    col_letter,
    format_a1,
    parse_a1,
    quote_sheet_name,
)
from app.document_engine.providers.google_drive_base import DocNotFound, ExportTooLarge
from app.document_engine.providers.google_sheets import (
    SPREADSHEET_URL,
    GoogleSheetsProvider,
    SheetApiError,
    SpreadsheetNotFound,
)
from app.services import google_oauth_service

READ_CELL_CAP = 200_000
WRITE_CELL_CAP = 50_000
CELL_TEXT_MAX = 50_000
MAX_TAB_NAME_LENGTH = 100
MAX_TABS = 50
MAX_GRID_ROWS = 50_000
MAX_GRID_COLUMNS = 500
EXPORT_SIZE_LIMIT = 10 * 1024 * 1024

SPREADSHEET_EXPORT_FORMATS = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}


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


def _check_tab_name(name) -> None:
    if not isinstance(name, str) or not 1 <= len(name) <= MAX_TAB_NAME_LENGTH:
        raise tool_errors.invalid_request("tab name must be 1-100 characters")


def _check_duplicate_tab(name: str, tabs: list[SheetTab], exclude: SheetTab | None = None) -> None:
    for tab in tabs:
        if exclude is not None and tab.sheet_id == exclude.sheet_id:
            continue
        if tab.name.lower() == name.lower():
            raise tool_errors.invalid_request(
                f"a tab named '{name}' already exists; existing tabs: "
                f"{', '.join(tab.name for tab in tabs)}"
            )


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
    if not total:
        raise tool_errors.invalid_request("rows must contain at least one cell")
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


def _wrap_google_error(exc) -> tool_errors.ToolApiError:
    """Map a provider Google error to a typed google_api_error tool error."""
    if isinstance(exc, SheetApiError):
        status, message = exc.status, str(exc)
    else:  # raw HttpError from Drive-level operations
        status = exc.resp.status if exc.resp is not None else None
        message = f"Google Drive API {status}: {exc.reason}"
    return tool_errors.google_api_error(403 if status == 403 else 502, message)


def _sheet_errors(fn):
    @wraps(fn)
    def wrapped(db, tool_ctx, spreadsheet_id, *args, **kwargs):
        try:
            return fn(db, tool_ctx, spreadsheet_id, *args, **kwargs)
        except (SpreadsheetNotFound, DocNotFound):
            raise tool_errors.spreadsheet_not_found(spreadsheet_id) from None
        except (SheetApiError, HttpError) as exc:
            raise _wrap_google_error(exc) from None

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


def _check_bounds(r: A1Range, tab: SheetTab, range_str: str) -> None:
    if (r.end_row and r.end_row > tab.row_count) or (r.end_col and r.end_col > tab.column_count):
        raise tool_errors.invalid_request(
            f"range {range_str} exceeds bounds (max column {col_letter(tab.column_count)}, "
            f"max row {tab.row_count})"
        )


def _preview_ranges(tab: SheetTab):
    if tab.row_count * tab.column_count <= READ_CELL_CAP:
        yield quote_sheet_name(tab.name)
        return
    for start_col in range(1, tab.column_count + 1, READ_CELL_CAP):
        end_col = min(tab.column_count, start_col + READ_CELL_CAP - 1)
        rows_per_chunk = READ_CELL_CAP // (end_col - start_col + 1)
        for start_row in range(1, tab.row_count + 1, rows_per_chunk):
            end_row = min(tab.row_count, start_row + rows_per_chunk - 1)
            yield format_a1(A1Range(tab.name, start_row, start_col, end_row, end_col))


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
        range_a1 = quote_sheet_name(tab.name)
    else:
        range_a1 = _qualified_range(parsed, tab)
        _check_bounds(parsed, tab, range_str)
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
    data_end_row = parsed.start_row + len(rows) - 1
    data_end_col = parsed.start_col + max(map(len, rows)) - 1
    single_cell = parsed.start_row == parsed.end_row and parsed.start_col == parsed.end_col
    if not single_cell and (data_end_row > parsed.end_row or data_end_col > parsed.end_col):
        raise tool_errors.invalid_request(
            f"rows exceed range {range_str}: row extent ends at row {data_end_row}, "
            f"column {col_letter(data_end_col)}"
        )
    end_row = max(parsed.end_row, data_end_row)
    end_col = max(parsed.end_col, data_end_col)
    provider = _provider(db, tool_ctx)
    tab = _resolve_sheet(_tabs(provider.get_spreadsheet(spreadsheet_id)), sheet)
    range_a1 = _qualified_range(parsed, tab)
    if end_row > tab.row_count or end_col > tab.column_count:
        provider.ensure_grid(
            spreadsheet_id,
            tab.sheet_id,
            max(tab.row_count, end_row),
            max(tab.column_count, end_col),
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
    _check_bounds(parsed, tab, range_str)
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
        needle = find if match_case else find.lower()
        occurrences = 0
        for tab in targets:
            for range_a1 in _preview_ranges(tab):
                for row in provider.get_values(spreadsheet_id, range_a1):
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


# ---- Tab management ----


def _check_grid_size(rows, cols) -> None:
    if not isinstance(rows, int) or isinstance(rows, bool) or not 1 <= rows <= MAX_GRID_ROWS:
        raise tool_errors.invalid_request(f"rows must be between 1 and {MAX_GRID_ROWS:,}")
    if not isinstance(cols, int) or isinstance(cols, bool) or not 1 <= cols <= MAX_GRID_COLUMNS:
        raise tool_errors.invalid_request(f"cols must be between 1 and {MAX_GRID_COLUMNS:,}")


@_sheet_errors
def add_sheet(db, tool_ctx, spreadsheet_id, name, rows=1000, cols=26) -> dict:
    _check_tab_name(name)
    _check_grid_size(rows, cols)
    provider = _provider(db, tool_ctx)
    tabs = _tabs(provider.get_spreadsheet(spreadsheet_id))
    _check_duplicate_tab(name, tabs)
    provider.batch_update(
        spreadsheet_id,
        [
            {
                "addSheet": {
                    "properties": {
                        "title": name,
                        "gridProperties": {"rowCount": rows, "columnCount": cols},
                    }
                }
            }
        ],
    )
    return {
        "spreadsheet_id": spreadsheet_id,
        "sheet": name,
        "summary": f"Added tab '{name}' with {rows} rows and {cols} columns",
    }


@_sheet_errors
def update_sheet(db, tool_ctx, spreadsheet_id, name, new_name=None, hidden=None) -> dict:
    if new_name is None and hidden is None:
        raise tool_errors.invalid_request("nothing to update: provide new_name and/or hidden")
    if hidden is not None and not isinstance(hidden, bool):
        raise tool_errors.invalid_request("hidden must be a boolean")
    if new_name is not None:
        _check_tab_name(new_name)
    provider = _provider(db, tool_ctx)
    tabs = _tabs(provider.get_spreadsheet(spreadsheet_id))
    tab = _resolve_sheet(tabs, name)
    if new_name is not None:
        _check_duplicate_tab(new_name, tabs, exclude=tab)
    properties: dict = {"sheetId": tab.sheet_id}
    fields = []
    if new_name is not None:
        properties["title"] = new_name
        fields.append("title")
    if hidden is not None:
        properties["hidden"] = hidden
        fields.append("hidden")
    provider.batch_update(
        spreadsheet_id,
        [{"updateSheetProperties": {"properties": properties, "fields": ",".join(fields)}}],
    )
    if new_name is not None and hidden is not None:
        summary = f"Renamed tab '{name}' to '{new_name}' and {'hidden' if hidden else 'unhidden'}"
    elif new_name is not None:
        summary = f"Renamed tab '{name}' to '{new_name}'"
    else:
        summary = f"{'Hidden' if hidden else 'Unhidden'} tab '{name}'"
    return {
        "spreadsheet_id": spreadsheet_id,
        "sheet": new_name if new_name is not None else name,
        "summary": summary,
    }


@_sheet_errors
def delete_sheet(db, tool_ctx, spreadsheet_id, name, confirm=False) -> dict:
    provider = _provider(db, tool_ctx)
    tabs = _tabs(provider.get_spreadsheet(spreadsheet_id))
    tab = _resolve_sheet(tabs, name)
    if len(tabs) == 1:
        raise tool_errors.invalid_request("cannot delete the only tab in the spreadsheet")
    if not confirm:
        raise tool_errors.confirmation_required(
            {"sheet": name, "rows": tab.row_count, "columns": tab.column_count}
        )
    provider.batch_update(spreadsheet_id, [{"deleteSheet": {"sheetId": tab.sheet_id}}])
    return {
        "spreadsheet_id": spreadsheet_id,
        "sheet": name,
        "summary": f"Deleted tab '{name}'",
    }


# ---- Spreadsheet-level operations ----


@_sheet_errors
def get_spreadsheet(db, tool_ctx, spreadsheet_id) -> dict:
    provider = _provider(db, tool_ctx)
    meta = provider.get_spreadsheet(spreadsheet_id)
    file_meta = provider.get_file_metadata(spreadsheet_id)
    resolved_id = meta.get("spreadsheetId", spreadsheet_id)
    return {
        "spreadsheet_id": resolved_id,
        "title": file_meta.get("name") or meta.get("properties", {}).get("title", ""),
        "url": SPREADSHEET_URL.format(resolved_id),
        "modified_time": file_meta.get("modified_time", ""),
        "sheets": [
            {
                "name": tab.name,
                "sheet_id": tab.sheet_id,
                "rows": tab.row_count,
                "columns": tab.column_count,
                "hidden": tab.hidden,
            }
            for tab in _tabs(meta)
        ],
    }


def create_spreadsheet(db, tool_ctx, title, sheets=None, folder_id=None) -> dict:
    names = []
    if sheets is not None:
        if not isinstance(sheets, list) or any(not isinstance(n, str) for n in sheets):
            raise tool_errors.invalid_request("sheets must be a list of tab name strings")
        if len(sheets) > MAX_TABS:
            raise tool_errors.invalid_request(
                f"at most {MAX_TABS} tab names allowed; got {len(sheets)}"
            )
        tabs_so_far: list[SheetTab] = []
        for index, tab_name in enumerate(sheets):
            _check_tab_name(tab_name)
            _check_duplicate_tab(tab_name, tabs_so_far)
            tabs_so_far.append(SheetTab(tab_name, index, 0, 0, False))
            names.append(tab_name)
    provider = _provider(db, tool_ctx)
    try:
        created = provider.create_spreadsheet(title, names)
        spreadsheet_id = created.get("spreadsheetId", "")
        if folder_id:
            provider.move_file(spreadsheet_id, folder_id)
    except (SheetApiError, HttpError) as exc:
        raise _wrap_google_error(exc) from None
    except DocNotFound:
        raise tool_errors.invalid_request(
            f"folder {folder_id} not found or not accessible (drive.file scope: only "
            "app-created or Picker-granted items are visible)"
        ) from None
    return {
        "spreadsheet_id": spreadsheet_id,
        "url": created.get("spreadsheetUrl") or SPREADSHEET_URL.format(spreadsheet_id),
        "title": title,
        "sheets": [
            sheet.get("properties", {}).get("title", "") for sheet in created.get("sheets", [])
        ],
    }


def search_spreadsheets(db, tool_ctx, query="", limit=10, folder_id=None) -> list[dict]:
    provider = _provider(db, tool_ctx)
    try:
        return provider.search_spreadsheets(query, limit, folder_id)
    except (SheetApiError, HttpError) as exc:
        raise _wrap_google_error(exc) from None


@_sheet_errors
def trash_spreadsheet(db, tool_ctx, spreadsheet_id, confirm=False) -> dict:
    if not confirm:
        raise tool_errors.confirmation_required(
            {
                "operation": "delete",
                "spreadsheet_id": spreadsheet_id,
                "effect": "Moves the spreadsheet to Google Drive trash (recoverable in Drive).",
            }
        )
    provider = _provider(db, tool_ctx)
    provider.trash_document(spreadsheet_id)
    return {
        "spreadsheet_id": spreadsheet_id,
        "url": SPREADSHEET_URL.format(spreadsheet_id),
        "summary": "Spreadsheet moved to Google Drive trash",
    }


@_sheet_errors
def restore_spreadsheet(db, tool_ctx, spreadsheet_id) -> dict:
    provider = _provider(db, tool_ctx)
    provider.untrash_document(spreadsheet_id)
    return {
        "spreadsheet_id": spreadsheet_id,
        "url": SPREADSHEET_URL.format(spreadsheet_id),
        "summary": "Spreadsheet restored from Google Drive trash",
    }


@_sheet_errors
def rename_spreadsheet(db, tool_ctx, spreadsheet_id, title) -> dict:
    provider = _provider(db, tool_ctx)
    provider.rename_file(spreadsheet_id, title)
    return {
        "spreadsheet_id": spreadsheet_id,
        "url": SPREADSHEET_URL.format(spreadsheet_id),
        "summary": f"Renamed spreadsheet to '{title}'",
    }


@_sheet_errors
def move_spreadsheet(db, tool_ctx, spreadsheet_id, folder_id, remove_current=True) -> dict:
    provider = _provider(db, tool_ctx)
    try:
        provider.move_file(spreadsheet_id, folder_id, remove_current)
    except DocNotFound as exc:
        if folder_id in str(exc):
            raise tool_errors.invalid_request(
                f"folder {folder_id} not found or not accessible (drive.file scope: only "
                "app-created or Picker-granted items are visible)"
            ) from None
        raise
    return {
        "spreadsheet_id": spreadsheet_id,
        "url": SPREADSHEET_URL.format(spreadsheet_id),
        "summary": f"Moved spreadsheet into folder {folder_id}",
    }


@_sheet_errors
def copy_spreadsheet(db, tool_ctx, spreadsheet_id, title=None) -> dict:
    provider = _provider(db, tool_ctx)
    provider.get_spreadsheet(spreadsheet_id)
    copied = provider.copy_spreadsheet(spreadsheet_id, title)
    return {
        "spreadsheet_id": copied["id"],
        "url": copied["url"],
        "summary": f"Copied spreadsheet as '{copied['name']}'",
    }


@_sheet_errors
def share_spreadsheet(db, tool_ctx, spreadsheet_id, email, role, notify=True) -> dict:
    provider = _provider(db, tool_ctx)
    provider.get_spreadsheet(spreadsheet_id)
    permission = provider.share_file(spreadsheet_id, email, role, notify)
    return {
        "spreadsheet_id": spreadsheet_id,
        "permission_id": permission["id"],
        "email": email,
        "role": role,
        "summary": f"Shared spreadsheet with {email} as {role}",
    }


@_sheet_errors
def list_spreadsheet_permissions(db, tool_ctx, spreadsheet_id) -> list[dict]:
    provider = _provider(db, tool_ctx)
    provider.get_spreadsheet(spreadsheet_id)
    return provider.list_permissions(spreadsheet_id)


@_sheet_errors
def export_spreadsheet(db, tool_ctx, spreadsheet_id, file_format) -> dict:
    mime = SPREADSHEET_EXPORT_FORMATS.get(file_format)
    if mime is None:
        raise tool_errors.invalid_request("format must be 'csv', 'xlsx', or 'pdf'")
    provider = _provider(db, tool_ctx)
    provider.get_spreadsheet(spreadsheet_id)
    try:
        data = provider.export_file(spreadsheet_id, mime)
    except ExportTooLarge:
        raise tool_errors.invalid_request(
            "Export too large: exceeds Google's 10MB export limit"
        ) from None
    if len(data) > EXPORT_SIZE_LIMIT:
        raise tool_errors.invalid_request(
            f"Export too large ({len(data)} bytes); limit is {EXPORT_SIZE_LIMIT} bytes"
        )
    return {
        "format": file_format,
        "mime": mime,
        "size_bytes": len(data),
        "content_base64": base64.b64encode(data).decode("ascii"),
        "filename": f"spreadsheet.{file_format}",
    }
