"""Pure spreadsheet validation helpers and Sheets provider construction."""

from typing import NamedTuple

from sqlalchemy.orm import Session

from app.core import tool_errors
from app.document_engine.a1 import A1Range
from app.document_engine.providers.google_sheets import GoogleSheetsProvider
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
        grid = properties["gridProperties"]
        tabs.append(
            SheetTab(
                properties["title"],
                properties["sheetId"],
                grid["rowCount"],
                grid["columnCount"],
                properties.get("hidden", False),
            )
        )
    return tabs


def _resolve_sheet(tabs: list[SheetTab], sheet: str | None) -> SheetTab:
    for tab in tabs:
        if (sheet is None and not tab.hidden) or (sheet is not None and tab.name == sheet):
            return tab
    raise tool_errors.sheet_not_found(sheet if sheet is not None else "<default>", [
        tab.name for tab in tabs
    ])


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
