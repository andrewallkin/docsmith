"""Google document JSON -> AST (+ outline). Inverse of the renderer pipeline."""

from dataclasses import dataclass

from app.document_engine.ast import (
    Document,
    Heading,
    ListBlock,
    ListItem,
    PageBreak,
    Paragraph,
    Table,
    Text,
)

NAMED_STYLE_LEVELS = {"HEADING_1": 1, "HEADING_2": 2, "HEADING_3": 3, "TITLE": 1}


@dataclass
class OutlineEntry:
    heading: str
    level: int
    start_index: int  # heading paragraph start
    end_index: int  # heading paragraph end (inclusive of its newline)


def _text_runs(paragraph_element: dict) -> list[Text]:
    runs: list[Text] = []
    for element in paragraph_element.get("elements", []):
        page_break = element.get("pageBreak")
        text_run = element.get("textRun")
        if page_break is not None:
            runs.append(Text(content="---"))
        elif text_run is not None:
            content = text_run.get("content", "")
            if not content:
                continue
            style = text_run.get("textStyle", {}) or {}
            runs.append(
                Text(
                    content=content,
                    bold=bool(style.get("bold")),
                    italic=bool(style.get("italic")),
                    href=(style.get("link") or {}).get("url") if style.get("link") else None,
                )
            )
    return runs


def _strip(runs: list[Text]) -> list[Text]:
    """Drop paragraph-terminating newlines and empty runs."""
    cleaned = [Text(**vars(r)) for r in runs]
    for run in cleaned:
        run.content = run.content.rstrip("\n")
    return [run for run in cleaned if run.content]


def document_json_to_ast(doc: dict) -> tuple[Document, list[OutlineEntry]]:
    """Convert a documents.get JSON dump to an AST plus a heading outline.

    Newline characters terminate paragraphs and are not represented as Text
    runs in the AST; the markdown writer re-adds them.
    """
    body = doc.get("body", {})
    document = Document(title=doc.get("title"))
    outline: list[OutlineEntry] = []

    # listId -> ordered?, derived from the document's lists map (glyph types).
    ordered_lists = _collect_ordered_lists(doc)

    open_list: ListBlock | None = None

    def close_list():
        nonlocal open_list
        if open_list is not None:
            document.children.append(open_list)
            open_list = None

    for element in body.get("content", []):
        if "table" in element:
            close_list()
            document.children.append(_convert_table(element["table"]))
            continue
        if "sectionBreak" in element:
            continue
        paragraph = element.get("paragraph")
        if paragraph is None:
            continue
        start = element.get("startIndex", 0)
        end = element.get("endIndex", 0)
        if any("pageBreak" in pe for pe in paragraph.get("elements", [])):
            close_list()
            document.children.append(PageBreak())
            continue
        runs = _text_runs(paragraph)
        # paragraph text without trailing newline
        text = "".join(r.content for r in runs)
        if text.endswith("\n"):
            text = text[:-1]
            if runs and runs[-1].content.endswith("\n"):
                runs[-1].content = runs[-1].content[:-1] or ""
        if not text:
            continue
        named = (paragraph.get("paragraphStyle") or {}).get("namedStyleType", "")
        level = NAMED_STYLE_LEVELS.get(named)
        if named == "TITLE" or level is not None:
            close_list()
            document.children.append(Heading(level=min(level or 1, 3), children=_strip(runs)))
            outline.append(
                OutlineEntry(
                    heading=text,
                    level=level or 1,
                    start_index=start,
                    end_index=end,
                )
            )
        elif "bullet" in paragraph:
            item = ListItem(children=_strip(runs))
            if open_list is None:
                ordered = ordered_lists.get(paragraph["bullet"]["listId"], False)
                open_list = ListBlock(ordered=ordered)
            open_list.items.append(item)
        else:
            close_list()
            document.children.append(Paragraph(children=_strip(runs)))

    close_list()
    return document, outline


def _collect_ordered_lists(doc: dict) -> dict[str, bool]:
    """Numbered bullet presets use glyph types like DECIMAL/GLYPH_DECIMAL."""
    ordered: dict[str, bool] = {}
    for list_id, node in (doc.get("lists") or {}).items():
        nesting = (node.get("listProperties") or {}).get("nestingLevels") or []
        glyph = (nesting[0] or {}).get("glyphType", "") if nesting else ""
        ordered[list_id] = glyph.removeprefix("GLYPH_") in (
            "DECIMAL",
            "ALPHABET",
            "ROMAN",
            "ROMAN_LOWER",
            "ALPHABET_LOWER",
            "DECIMAL_OFFSET",
            "DECIMAL_CIRCLED",
        )
    return ordered


def _convert_table(table: dict) -> Table:
    converted = Table()
    rows: list[list[list[Text]]] = []
    for row in table.get("tableRows", []):
        cells: list[list[Text]] = []
        for cell in row.get("tableCells", []):
            cell_runs: list[Text] = []
            for paragraph in cell.get("content", []):
                pe = paragraph.get("paragraph")
                if pe:
                    cell_runs.extend(_text_runs(pe))
            for run in cell_runs:
                run.content = run.content.replace("\n", " ")
            cells.append([run for run in cell_runs if run.content.strip()])
        rows.append(cells)
    # Google tables: first row may be styled as header; markdown-wise we emit
    # the first row as the header row when the table has >1 row and the first
    # row's cells are bold — otherwise keep all rows as body with a synthetic
    # empty header only if there is header styling.
    if len(rows) >= 2 and _row_is_bold(rows[0]):
        converted.header = rows[0]
        converted.rows = rows[1:]
    elif len(rows) == 1 and _row_is_bold(rows[0]):
        converted.header = rows[0]
    else:
        converted.rows = rows
    return converted


def _row_is_bold(row: list[list[Text]]) -> bool:
    runs = [run for cell in row for run in cell]
    return bool(runs) and all(run.bold for run in runs)
