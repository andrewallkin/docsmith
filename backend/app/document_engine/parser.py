"""Markdown → AST via markdown-it-py (tables enabled; no regex-based formatting)."""

from markdown_it import MarkdownIt

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


def _parse_inline(inline_token) -> list[Text]:
    """Walk an inline token's children building styled runs."""
    runs: list[Text] = []
    bold = False
    italic = False
    href: str | None = None

    for child in inline_token.children or []:
        ctype = child.type
        if ctype == "strong_open":
            bold = True
        elif ctype == "strong_close":
            bold = False
        elif ctype == "em_open":
            italic = True
        elif ctype == "em_close":
            italic = False
        elif ctype == "link_open":
            href = child.attrGet("href")
        elif ctype == "link_close":
            href = None
        elif ctype in ("text", "code_inline"):
            content = child.content
            if content:
                runs.append(Text(content=content, bold=bold, italic=italic, href=href))
        elif ctype in ("hardbreak", "softbreak"):
            runs.append(Text(content="\n"))

    return runs


def _merge_runs(runs: list[Text]) -> list[Text]:
    """Merge adjacent runs with identical styling (link targets are preserved)."""
    merged: list[Text] = []
    for run in runs:
        prev = merged[-1] if merged else None
        if (
            prev
            and prev.href == run.href
            and prev.href is None
            and prev.bold == run.bold
            and prev.italic == run.italic
            and (prev.content.endswith("\n") is False)
            and (run.content.startswith("\n") is False)
        ):
            prev.content += run.content
        else:
            merged.append(Text(**vars(run)))
    return merged


def parse(markdown: str) -> Document:
    md = MarkdownIt("commonmark").enable("table")
    tokens = md.parse(markdown)
    doc = Document()

    # Document title: a leading H1 followed by a blank line is commonly the
    # title; MVP keeps H1s as headings and sets the title separately via the
    # API, so nothing special-cased here.

    i = 0
    n = len(tokens)
    while i < n:
        token = tokens[i]
        if token.type == "heading_open":
            level = int(token.tag[1])
            inline = tokens[i + 1] if i + 1 < n else None
            doc.children.append(
                Heading(level=min(level, 3), children=_merge_runs(_parse_inline(inline)))
            )
            i += 3  # heading_open, inline, heading_close
        elif token.type in ("bullet_list_open", "ordered_list_open"):
            block, i = _parse_list(tokens, i)
            doc.children.append(block)
        elif token.type == "table_open":
            table, i = _parse_table(tokens, i)
            doc.children.append(table)
        elif token.type == "hr":
            doc.children.append(PageBreak())
            i += 1
        elif token.type == "paragraph_open":
            inline = tokens[i + 1] if i + 1 < n else None
            doc.children.append(
                Paragraph(children=_merge_runs(_parse_inline(inline)))
            )
            i += 3  # paragraph_open, inline, paragraph_close
        elif token.type in (
            "heading_close",
            "paragraph_close",
            "bullet_list_close",
            "ordered_list_close",
            "list_item_close",
            "table_close",
            "thead_close",
            "tbody_close",
            "tr_close",
            "inline",
        ):
            i += 1
        else:
            i += 1

    return doc


def _parse_list(tokens: list, start: int) -> tuple[ListBlock, int]:
    ordered = tokens[start].type == "ordered_list_open"
    block = ListBlock(ordered=ordered)
    i = start + 1
    n = len(tokens)
    while i < n:
        token = tokens[i]
        if token.type == "list_item_open":
            # item content is its inline paragraph (plus optional nested lists,
            # which are out of MVP scope and rendered as separate items)
            j = i + 1
            while j < n and tokens[j].type != "list_item_close":
                if tokens[j].type == "paragraph_open":
                    inline = tokens[j + 1]
                    block.items.append(ListItem(children=_merge_runs(_parse_inline(inline))))
                elif tokens[j].type in ("bullet_list_open", "ordered_list_open"):
                    # flatten nested lists as sibling items
                    nested, j = _parse_list(tokens, j)
                    block.items.extend(nested.items)
                    continue
                j += 1
            i = j + 1
        elif token.type in ("bullet_list_close", "ordered_list_close"):
            return block, i + 1
        else:
            i += 1
    return block, i


def _parse_table(tokens: list, start: int) -> tuple[Table, int]:
    table = Table()
    i = start + 1
    n = len(tokens)
    current_row: list[list[Text]] | None = None
    in_header = False
    while i < n:
        token = tokens[i]
        if token.type == "thead_open":
            in_header = True
        elif token.type == "thead_close":
            in_header = False
        elif token.type == "tr_open":
            current_row = []
        elif token.type == "tr_close":
            if current_row is not None:
                if in_header:
                    table.header = current_row
                else:
                    table.rows.append(current_row)
            current_row = None
        elif token.type in ("th_open", "td_open"):
            inline = tokens[i + 1] if i + 1 < n else None
            cell_runs = _merge_runs(_parse_inline(inline))
            if current_row is not None:
                current_row.append(cell_runs)
        i += 1
        if token.type == "table_close":
            break
    return table, i
