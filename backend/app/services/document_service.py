"""Document orchestration for the agent tool API: search / read / create /
append / replace_section / regenerate / delete.

Every mutation accepts an optional required_revision_id; when supplied, the
document's current revision is checked first (read-back) and the write is
submitted with WriteControl.requiredRevisionId. Mutations return the new
revision id (read back after the write).
"""

from sqlalchemy.orm import Session

from app.core import tool_errors
from app.document_engine import parser
from app.document_engine.ast import Document as AstDocument
from app.document_engine.ast import Heading
from app.document_engine.markdown_writer import ast_to_markdown
from app.document_engine.providers.google_docs import (
    DocNotFound,
    GoogleDocsProvider,
    RevisionConflict,
)
from app.document_engine.reader import OutlineEntry, document_json_to_ast
from app.document_engine.renderer import render_blocks, render_new_document
from app.document_engine.themes import get_theme
from app.services import google_oauth_service

DOC_URL = "https://docs.google.com/document/d/{}/edit"


def _provider(db: Session, tool_ctx) -> GoogleDocsProvider:
    try:
        credentials = google_oauth_service.get_credentials(db, tool_ctx.connection)
    except google_oauth_service.GoogleReconnectRequired:
        raise tool_errors.google_reconnect_required() from None
    return GoogleDocsProvider(credentials)


def _get_or_not_found(provider: GoogleDocsProvider, document_id: str) -> dict:
    try:
        return provider.get_document(document_id)
    except DocNotFound:
        raise tool_errors.doc_not_found(document_id) from None


def _check_revision(doc: dict, required_revision_id: str | None) -> None:
    if required_revision_id and required_revision_id != doc.get("revisionId"):
        raise tool_errors.conflict_detected(doc.get("revisionId", ""))


def _outline_payload(outline: list[OutlineEntry]) -> list[dict]:
    return [
        {"heading": entry.heading, "level": entry.level, "start_index": entry.start_index}
        for entry in outline
    ]


def _summarize_blocks(blocks: list) -> str:
    counts: dict[str, int] = {}
    for block in blocks:
        name = type(block).__name__
        counts[name] = counts.get(name, 0) + 1
    if not counts:
        return "no blocks"
    labels = [
        f"{count} {name.lower()}{'' if count == 1 else 's'}" for name, count in counts.items()
    ]
    return ", ".join(labels)


def search_documents(
    db: Session,
    tool_ctx,
    query: str,
    limit: int = 10,
    full_text: str | None = None,
    folder_id: str | None = None,
) -> list[dict]:
    provider = _provider(db, tool_ctx)
    return provider.search_documents(query, limit, full_text=full_text, folder_id=folder_id)


def read_document(db: Session, tool_ctx, document_id: str) -> dict:
    provider = _provider(db, tool_ctx)
    doc = _get_or_not_found(provider, document_id)
    ast, outline = document_json_to_ast(doc)
    return {
        "id": doc.get("documentId", document_id),
        "title": doc.get("title", ""),
        "url": DOC_URL.format(doc.get("documentId", document_id)),
        "revision_id": doc.get("revisionId", ""),
        "markdown": ast_to_markdown(ast),
        "outline": _outline_payload(outline),
    }


def create_document(
    db: Session, tool_ctx, title: str, markdown: str, theme_name: str | None
) -> dict:
    provider = _provider(db, tool_ctx)
    theme = get_theme(theme_name)
    ast = parser.parse(markdown)
    blocks = ast.children
    created = provider.create_document(title)
    document_id = created["documentId"]
    steps, doc_style = render_new_document(
        AstDocument(title=title, children=blocks), theme
    )
    if doc_style:
        provider.batch_update(document_id, doc_style)
    try:
        provider.execute_plan(document_id, steps, steps[0].start_cursor, theme)
    except RevisionConflict:
        raise tool_errors.conflict_detected("") from None
    except DocNotFound:
        raise tool_errors.doc_not_found(document_id) from None
    doc = provider.get_document_fresh(document_id)
    return {
        "document_id": document_id,
        "document_url": DOC_URL.format(document_id),
        "revision_id": doc.get("revisionId", ""),
        "summary": f"Created document '{title}' ({_summarize_blocks(blocks)})",
    }


def append_markdown(
    db: Session, tool_ctx, document_id: str, markdown: str, required_revision_id: str | None
) -> dict:
    provider = _provider(db, tool_ctx)
    doc = _get_or_not_found(provider, document_id)
    _check_revision(doc, required_revision_id)
    insert_index = _append_position(doc)
    ast = parser.parse(markdown)
    theme = get_theme(None)
    steps = render_blocks(ast.children, theme, start_index=insert_index)
    if not steps:
        raise tool_errors.invalid_request("Nothing to append: markdown is empty")
    try:
        provider.execute_plan(document_id, steps, insert_index, theme, required_revision_id)
    except RevisionConflict:
        raise tool_errors.conflict_detected(doc.get("revisionId", "")) from None
    except DocNotFound:
        raise tool_errors.doc_not_found(document_id) from None
    new_doc = provider.get_document(document_id)
    return {
        "document_id": document_id,
        "document_url": DOC_URL.format(document_id),
        "revision_id": new_doc.get("revisionId", ""),
        "summary": f"Appended content at end of document ({_summarize_blocks(ast.children)})",
    }


def replace_section(
    db: Session,
    tool_ctx,
    document_id: str,
    section_heading: str,
    markdown: str,
    theme_name: str | None,
    required_revision_id: str | None,
) -> dict:
    provider = _provider(db, tool_ctx)
    doc = _get_or_not_found(provider, document_id)
    _check_revision(doc, required_revision_id)
    ast, outline = document_json_to_ast(doc)
    section = _find_section(outline, section_heading)
    if section is None:
        raise tool_errors.invalid_request(
            f"Section heading '{section_heading}' not found in document",
            {"outline": [e.heading for e in outline]},
        )
    theme = get_theme(theme_name)
    new_ast = parser.parse(markdown)
    section_end = _section_end(doc, outline, section)
    provider.batch_update(
        document_id,
        [
            {
                "deleteContentRange": {
                    "range": {"startIndex": section.start_index, "endIndex": section_end}
                }
            }
        ],
        required_revision_id,
    )
    if new_ast.children:
        steps = render_blocks(new_ast.children, theme, start_index=section.start_index)
        try:
            provider.execute_plan(
                document_id, steps, section.start_index, theme, required_revision_id
            )
        except RevisionConflict:
            raise tool_errors.conflict_detected(doc.get("revisionId", "")) from None
        except DocNotFound:
            raise tool_errors.doc_not_found(document_id) from None
    new_doc = provider.get_document(document_id)
    return {
        "document_id": document_id,
        "document_url": DOC_URL.format(document_id),
        "revision_id": new_doc.get("revisionId", ""),
        "summary": (
            f"Replaced section '{section.heading}' "
            f"({_summarize_blocks(new_ast.children)})"
        ),
    }


def regenerate_document(
    db: Session,
    tool_ctx,
    document_id: str,
    markdown: str,
    theme_name: str | None,
    required_revision_id: str | None,
    confirm: bool,
) -> dict:
    provider = _provider(db, tool_ctx)
    doc = _get_or_not_found(provider, document_id)
    new_ast = parser.parse(markdown)
    if not confirm:
        current_ast, _outline = document_json_to_ast(doc)
        preview = {
            "operation": "regenerate",
            "title": doc.get("title", ""),
            "current_revision_id": doc.get("revisionId", ""),
            "current_blocks": len(current_ast.children),
            "new_blocks": len(new_ast.children),
            "new_headings": [
                "".join(run.content for run in b.children)
                for b in new_ast.children
                if isinstance(b, Heading)
            ],
        }
        raise tool_errors.confirmation_required(preview)
    _check_revision(doc, required_revision_id)
    theme = get_theme(theme_name)
    delete_end = _last_paragraph_end(doc)
    if delete_end > 1:
        provider.batch_update(
            document_id,
            [{"deleteContentRange": {"range": {"startIndex": 1, "endIndex": delete_end}}}],
            required_revision_id,
        )
    if new_ast.children:
        steps = render_blocks(new_ast.children, theme, start_index=1)
        try:
            provider.execute_plan(document_id, steps, 1, theme, required_revision_id)
        except RevisionConflict:
            raise tool_errors.conflict_detected(doc.get("revisionId", "")) from None
        except DocNotFound:
            raise tool_errors.doc_not_found(document_id) from None
    else:
        # keep the body valid: always end with a paragraph
        provider.batch_update(
            document_id, [{"insertText": {"location": {"index": 1}, "text": "\n"}}]
        )
    new_doc = provider.get_document(document_id)
    return {
        "document_id": document_id,
        "document_url": DOC_URL.format(document_id),
        "revision_id": new_doc.get("revisionId", ""),
        "summary": f"Regenerated document body ({_summarize_blocks(new_ast.children)})",
    }


def delete_document(
    db: Session, tool_ctx, document_id: str, confirm: bool
) -> dict:
    if not confirm:
        raise tool_errors.confirmation_required(
            {
                "operation": "delete",
                "document_id": document_id,
                "effect": "Moves the document to Google Drive trash (recoverable in Drive).",
            }
        )
    provider = _provider(db, tool_ctx)
    try:
        provider.trash_document(document_id)
    except DocNotFound:
        raise tool_errors.doc_not_found(document_id) from None
    return {
        "document_id": document_id,
        "document_url": DOC_URL.format(document_id),
        "summary": "Document moved to Google Drive trash",
    }


def _append_position(doc: dict) -> int:
    """Index just before the final paragraph's terminating newline."""
    content = doc.get("body", {}).get("content", [])
    for element in reversed(content):
        if "paragraph" in element:
            return element["endIndex"] - 1
    return 1


def _find_section(outline: list[OutlineEntry], section_heading: str) -> OutlineEntry | None:
    target = section_heading.strip().lower()
    for entry in outline:
        if entry.heading.strip().lower() == target:
            return entry
    return None


def _section_end(doc: dict, outline: list[OutlineEntry], section: OutlineEntry) -> int:
    """End index of the section: start of the next heading with level <= the
    section's level, else the end of the document's last paragraph (real docs
    always end with a section break element after it, which stays)."""
    for entry in outline:
        if entry.start_index > section.end_index and entry.level <= section.level:
            return entry.start_index
    return _last_paragraph_end(doc)


def _last_paragraph_end(doc: dict) -> int:
    content = doc.get("body", {}).get("content", [])
    for element in reversed(content):
        if "paragraph" in element:
            return element["endIndex"]
        if "sectionBreak" in element:
            return element["startIndex"]
    return 1
