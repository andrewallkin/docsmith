"""Parse and format bounded A1 ranges without a spreadsheet dependency.

Whole-column ranges use end_row=0 for unbounded rows; whole-row ranges use
end_col=0 for unbounded columns. All other coordinates are 1-based inclusive.
"""

import re
from typing import NamedTuple


class A1Range(NamedTuple):
    sheet: str | None
    start_row: int
    start_col: int
    end_row: int
    end_col: int


class InvalidRangeError(ValueError):
    """An A1 range or column is invalid."""


def col_index(letters: str) -> int:
    """Convert ASCII column letters to a 1-based column index."""
    if not re.fullmatch(r"[A-Za-z]+", letters):
        raise InvalidRangeError(f"invalid column {letters!r}: expected column letters")
    result = 0
    for letter in letters.upper():
        result = result * 26 + ord(letter) - ord("A") + 1
    return result


def col_letter(n: int) -> str:
    """Convert a positive 1-based column index to uppercase letters."""
    if n < 1:
        raise InvalidRangeError(f"invalid column index {n}: expected a positive integer")
    result = ""
    while n:
        n, remainder = divmod(n - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def parse_a1(text: str) -> A1Range:
    """Parse a cell, rectangle, whole-column range, or whole-row range."""

    def invalid(reason: str) -> InvalidRangeError:
        return InvalidRangeError(f"invalid range '{text}': {reason}")

    source = text.strip()
    sheet = None
    body = source
    if source.startswith("'"):
        name = []
        i = 1
        while i < len(source):
            if source[i] == "'":
                if i + 1 < len(source) and source[i + 1] == "'":
                    name.append("'")
                    i += 2
                    continue
                break
            name.append(source[i])
            i += 1
        if i == len(source):
            raise invalid("sheet name quote is unclosed")
        if i + 1 >= len(source) or source[i + 1] != "!":
            raise invalid("quoted sheet name must be followed by !")
        sheet = "".join(name)
        body = source[i + 2 :]
        if not sheet:
            raise invalid("sheet name is empty")
    elif "!" in source:
        sheet, body = source.split("!", 1)
        if not sheet or re.search(r"[\s'!]", sheet):
            raise invalid("invalid sheet name; quote names containing spaces or apostrophes")

    parts = body.split(":")
    if not body or len(parts) > 2:
        raise invalid("expected a cell or a range with two endpoints")
    if any(not part for part in parts):
        raise invalid("range endpoint is unbounded; specify both endpoints")

    def row_number(digits: str) -> int:
        try:
            return int(digits)
        except ValueError as exc:
            raise invalid("row number is too large") from exc

    def endpoint(part: str) -> tuple[str, int, int]:
        match = re.fullmatch(r"([A-Za-z]+)([0-9]*)|([0-9]+)", part)
        if not match:
            raise invalid(f"invalid endpoint {part!r}")
        letters, row, row_only = match.groups()
        if row_only is not None:
            return "row", row_number(row_only), 1
        column = col_index(letters)
        if column > 500:
            raise invalid(f"column {letters!r} exceeds the 500-column limit")
        if row:
            return "cell", row_number(row), column
        return "column", 1, column

    start_kind, start_row, start_col = endpoint(parts[0])
    if len(parts) == 1:
        if start_kind == "row":
            raise invalid("whole-row ranges require two row endpoints")
        end_kind, end_row, end_col = start_kind, start_row, start_col
    else:
        end_kind, end_row, end_col = endpoint(parts[1])
    if start_kind != end_kind:
        raise invalid(
            "range endpoints must both be cells, columns, or rows; an endpoint is unbounded"
        )
    if start_row < 1 or end_row < 1:
        raise invalid("row numbers must be positive")
    if start_row > end_row or start_col > end_col:
        raise invalid("range bounds are inverted")
    if start_kind == "column":
        end_row = 0
    elif start_kind == "row":
        end_col = 0
    return A1Range(sheet, start_row, start_col, end_row, end_col)


def format_a1(r: A1Range) -> str:
    """Format an A1Range using canonical column letters and sheet quoting."""
    prefix = ""
    if r.sheet is not None:
        sheet = r.sheet
        if not re.fullmatch(r"[A-Za-z0-9_]+", sheet):
            sheet = "'" + sheet.replace("'", "''") + "'"
        prefix = f"{sheet}!"
    if r.end_row == 0:
        body = f"{col_letter(r.start_col)}:{col_letter(r.end_col)}"
    elif r.end_col == 0:
        body = f"{r.start_row}:{r.end_row}"
    else:
        start = f"{col_letter(r.start_col)}{r.start_row}"
        end = f"{col_letter(r.end_col)}{r.end_row}"
        body = start if start == end else f"{start}:{end}"
    return prefix + body
