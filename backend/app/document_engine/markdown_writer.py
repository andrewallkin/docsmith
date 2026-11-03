"""AST -> Markdown (for the read tool endpoint)."""

from app.document_engine.ast import (
    Document,
    Heading,
    ListBlock,
    PageBreak,
    Paragraph,
    Table,
    Text,
)


def _run_text(run: Text) -> str:
    text = run.content
    if run.href:
        text = f"[{text}]({run.href})"
    if run.bold and run.italic:
        text = f"***{text}***"
    elif run.bold:
        text = f"**{text}**"
    elif run.italic:
        text = f"*{text}*"
    return text


def _inline(runs: list[Text]) -> str:
    return "".join(_run_text(run) for run in runs)


def _table_md(table: Table) -> str:
    if table.header:
        header = table.header
        rows = table.rows
    else:
        header = table.rows[0] if table.rows else []
        rows = table.rows[1:] if len(table.rows) > 1 else []
    if not header:
        return ""
    lines = [
        "| " + " | ".join(_inline(cell).replace("\n", " ") for cell in header) + " |",
        "| " + " | ".join(["---"] * len(header)) + " |",
    ]
    for row in rows:
        cells = list(row) + [[]] * (len(header) - len(row)) if len(row) < len(header) else row
        lines.append("| " + " | ".join(_inline(cell).replace("\n", " ") for cell in cells) + " |")
    return "\n".join(lines)


def ast_to_markdown(doc: Document) -> str:
    parts: list[str] = []
    if doc.title:
        parts.append(f"# {doc.title}")
    for block in doc.children:
        if isinstance(block, Heading):
            parts.append("#" * block.level + " " + _inline(block.children))
        elif isinstance(block, Paragraph):
            parts.append(_inline(block.children))
        elif isinstance(block, ListBlock):
            marker = "1." if block.ordered else "-"
            for item in block.items:
                parts.append(f"{marker} {_inline(item.children)}")
        elif isinstance(block, Table):
            table_md = _table_md(block)
            if table_md:
                parts.append(table_md)
        elif isinstance(block, PageBreak):
            parts.append("---")
    return "\n\n".join(part for part in parts if part)
