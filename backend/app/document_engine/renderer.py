"""AST -> Google Docs batchUpdate execution plans.

Index semantics (from Google's Docs API documentation, verified):
- Within a single batchUpdate, each request's indices are interpreted against
  the document state produced by the previous requests in the same batch
  (sequential application). Text inserted earlier in the batch is therefore
  styled with ranges in post-insert coordinates.
- A 2x2 empty table inserted at S occupies S..S+12 (size = R*(2C+1) + 2 for
  R rows / C cols) and its empty cell paragraphs sit at
  S + 3 + r*(2*C+1) + 2*c.
- Cell writes are not allowed in the same batch that inserts the table, so a
  table is emitted as a TableStep: the provider inserts the structure, reads
  back the actual table extent, then fills/styles cells in a second batch.

All indices inside a step are RELATIVE to the step's virtual start position.
The provider maintains an actual document cursor and shifts indices by
(actual_cursor - virtual_cursor) before executing each step; after a TableStep
the actual cursor comes from the read-back, so drift in the predicted table
size never corrupts later steps.
"""

from dataclasses import dataclass, field

from app.document_engine.ast import (
    Document,
    Heading,
    ListBlock,
    PageBreak,
    Paragraph,
    Table,
    Text,
)
from app.document_engine.themes import Theme

BULLET_PRESET_UNORDERED = "BULLET_DISC_CIRCLE_SQUARE"
BULLET_PRESET_ORDERED = "NUMBERED_DECIMAL_NESTED"

NAMED_STYLES = {1: "HEADING_1", 2: "HEADING_2", 3: "HEADING_3"}


@dataclass
class BatchStep:
    """One batchUpdate of pre-computed requests (indices relative to start_cursor)."""

    requests: list[dict]
    start_cursor: int  # virtual cursor position when the step begins
    cursor: int  # virtual cursor position after the step


@dataclass
class TableStep:
    """Insert a table structure, then fill/style cells after read-back."""

    rows: int
    cols: int
    header: list[list[Text]]
    body_rows: list[list[list[Text]]]
    start_cursor: int
    cursor: int  # predicted virtual cursor after the table (provider uses actual)


Step = BatchStep | TableStep


def utf16_len(text: str) -> int:
    """Google Docs indices are UTF-16 code units."""
    return len(text.encode("utf-16-le")) // 2


def _rgb(hex_colour: str) -> dict:
    value = hex_colour.lstrip("#")
    return {
        "red": int(value[0:2], 16) / 255.0,
        "green": int(value[2:4], 16) / 255.0,
        "blue": int(value[4:6], 16) / 255.0,
    }


def _colour(rgb: dict) -> dict:
    return {"color": {"rgbColor": rgb}}


def _pt(magnitude: float) -> dict:
    return {"magnitude": magnitude, "unit": "PT"}


def _table_cell_insert_index(table_start: int, row: int, col: int, cols: int) -> int:
    """Insertion index for the paragraph of empty cell (row, col).

    Derived from Google's documented 2x2 index table (cell paragraph 0,0 sits
    at table_start + 3; row stride 2*C+1; column stride 2).
    """
    return table_start + 3 + row * (2 * cols + 1) + 2 * col


@dataclass
class _PlanBuilder:
    theme: Theme
    steps: list[Step] = field(default_factory=list)
    requests: list[dict] = field(default_factory=list)  # current batch, relative indices
    step_start: int = 0
    cursor: int = 0  # relative cursor within the current step

    def begin(self, position: int) -> None:
        self.step_start = position
        self.cursor = 0

    def flush(self) -> None:
        if self.requests:
            self.steps.append(
                BatchStep(
                    requests=self.requests,
                    start_cursor=self.step_start,
                    cursor=self.step_start + self.cursor,
                )
            )
            self.requests = []


def render_blocks(blocks: list, theme: Theme, start_index: int) -> list[Step]:
    """Render AST blocks into steps; content begins at document index start_index."""
    plan = _PlanBuilder(theme=theme)
    plan.begin(start_index)
    for block in blocks:
        _render_block(plan, block)
    plan.flush()
    return plan.steps


def _render_block(plan: _PlanBuilder, block) -> None:
    if isinstance(block, Heading):
        _render_heading(plan, block)
    elif isinstance(block, Paragraph):
        _render_paragraph(plan, block)
    elif isinstance(block, ListBlock):
        _render_list(plan, block)
    elif isinstance(block, Table):
        _render_table(plan, block)
    elif isinstance(block, PageBreak):
        plan.requests.append({"insertPageBreak": {"location": {"index": plan.cursor}}})
        plan.cursor += 1


def _finish_batch(plan: _PlanBuilder) -> None:
    """Close the current batch at the current relative cursor."""
    plan.flush(plan.cursor)


def _insert_text(plan: _PlanBuilder, text: str) -> None:
    plan.requests.append({"insertText": {"location": {"index": plan.cursor}, "text": text}})
    plan.cursor += utf16_len(text)


def _render_runs(
    plan: _PlanBuilder, runs: list[Text], base: int, size: float, bold: bool = False
) -> None:
    """Emit updateTextStyle requests for runs inserted starting at relative `base`."""
    offset = base
    for run in runs:
        length = utf16_len(run.content)
        if length and run.content != "\n":
            style: dict = {}
            fields: list[str] = []
            if run.bold or bold:
                style["bold"] = True
                fields.append("bold")
            if run.italic:
                style["italic"] = True
                fields.append("italic")
            if run.href:
                style["link"] = {"uri": run.href}
                fields.append("link")
            style["fontFamily"] = plan.theme.font
            style["fontSize"] = _pt(size)
            style["foregroundColor"] = _colour(_rgb(plan.theme.text_colour))
            fields.extend(["fontFamily", "fontSize", "foregroundColor"])
            plan.requests.append(
                {
                    "updateTextStyle": {
                        "range": {"startIndex": offset, "endIndex": offset + length},
                        "textStyle": style,
                        "fields": ",".join(fields),
                    }
                }
            )
        offset += length


def _render_heading(plan: _PlanBuilder, block: Heading) -> None:
    size = {
        1: plan.theme.heading_1_size,
        2: plan.theme.heading_2_size,
        3: plan.theme.heading_3_size,
    }[block.level]
    text = "".join(run.content for run in block.children)
    if not text:
        return
    start = plan.cursor
    _insert_text(plan, text + "\n")
    plan.requests.append(
        {
            "updateParagraphStyle": {
                "range": {"startIndex": start, "endIndex": plan.cursor},
                "paragraphStyle": {
                    "namedStyleType": NAMED_STYLES[block.level],
                    "spaceBelow": _pt(plan.theme.paragraph_spacing),
                },
                "fields": "namedStyleType,spaceBelow",
            }
        }
    )
    _render_runs(plan, block.children, start, size)


def _render_paragraph(plan: _PlanBuilder, block: Paragraph) -> None:
    text = "".join(run.content for run in block.children)
    if not text:
        return
    start = plan.cursor
    _insert_text(plan, text + "\n")
    plan.requests.append(
        {
            "updateParagraphStyle": {
                "range": {"startIndex": start, "endIndex": plan.cursor},
                "paragraphStyle": {"spaceBelow": _pt(plan.theme.paragraph_spacing)},
                "fields": "spaceBelow",
            }
        }
    )
    _render_runs(plan, block.children, start, plan.theme.body_size)


def _render_list(plan: _PlanBuilder, block: ListBlock) -> None:
    ranges: list[tuple[int, list[Text]]] = []
    for item in block.items:
        text = "".join(run.content for run in item.children)
        if not text:
            continue
        start = plan.cursor
        _insert_text(plan, text + "\n")
        ranges.append((start, item.children))
    if not ranges:
        return
    preset = BULLET_PRESET_ORDERED if block.ordered else BULLET_PRESET_UNORDERED
    plan.requests.append(
        {
            "createParagraphBullets": {
                "range": {"startIndex": ranges[0][0], "endIndex": plan.cursor},
                "bulletPreset": preset,
            }
        }
    )
    for start, children in ranges:
        _render_runs(plan, children, start, plan.theme.body_size)


def _render_table(plan: _PlanBuilder, block: Table) -> None:
    cols = max(
        len(block.header),
        max((len(row) for row in block.rows), default=0),
        1,
    )
    rows = len(block.rows) + (1 if block.header else 0)
    absolute_end = plan.step_start + plan.cursor + rows * (2 * cols + 1) + 2
    plan.flush()
    plan.steps.append(
        TableStep(
            rows=rows,
            cols=cols,
            header=block.header,
            body_rows=block.rows,
            start_cursor=plan.step_start,
            cursor=absolute_end,
        )
    )
    plan.begin(absolute_end)


def render_new_document(doc: Document, theme: Theme) -> tuple[list[Step], list[dict]]:
    """Steps for a fresh document plus leading document-style requests.

    The provider executes the document-style requests first (index-free), then
    the steps with cursor base 1 (a fresh document body starts with one empty
    paragraph at index 1). The document must be created with `title` first.
    """
    blocks = list(doc.children)
    title_text = (doc.title or "").strip()
    if title_text:
        first = blocks[0] if blocks else None
        first_heading_text = (
            "".join(run.content for run in first.children) if isinstance(first, Heading) else None
        )
        if not (first_heading_text and first_heading_text.strip().lower() == title_text.lower()):
            blocks.insert(0, Heading(level=1, children=[Text(content=title_text)]))
    steps = render_blocks(blocks, theme, start_index=1)
    return steps, _document_style_requests(theme)


def _document_style_requests(theme: Theme) -> list[dict]:
    return [
        {
            "updateDocumentStyle": {
                "documentStyle": {
                    "marginTop": _pt(theme.page_margin),
                    "marginBottom": _pt(theme.page_margin),
                    "marginLeft": _pt(theme.page_margin),
                    "marginRight": _pt(theme.page_margin),
                },
                "fields": "marginTop,marginBottom,marginLeft,marginRight",
            }
        }
    ]
