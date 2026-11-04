"""Document orchestration for the agent tool API: search / read / create /
append / replace_section / regenerate / delete / rename / restore, plus
Drive folder management (create / list / delete), moving documents
between folders, copying documents and PDF export.

Every mutation accepts an optional required_revision_id; when supplied, the
document's current revision is checked first (read-back) and the write is
submitted with WriteControl.requiredRevisionId. Mutations return the new
revision id (read back after the write).
"""

import base64

from sqlalchemy.orm import Session

from app.core import tool_errors
from app.document_engine import parser
from app.document_engine.ast import Document as AstDocument
from app.document_engine.ast import Heading
from app.document_engine.markdown_writer import ast_to_markdown
from app.document_engine.providers.google_docs import (
    DocNotFound,
    ExportTooLarge,
    GoogleDocsProvider,
    RevisionConflict,
)
from app.document_engine.reader import OutlineEntry, document_json_to_ast
from app.document_engine.renderer import render_blocks, render_new_document
from app.document_engine.themes import get_theme
from app.services import google_oauth_service

DOC_URL = "https://docs.google.com/document/d/{}/edit"
FOLDER_URL = "https://drive.google.com/drive/folders/{}"
EXPORT_SIZE_LIMIT = 10 * 1024 * 1024


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
        "created": True,
    }


def get_or_create_document(
    db: Session, tool_ctx, title: str, markdown: str, theme_name: str | None
) -> dict:
    provider = _provider(db, tool_ctx)
    existing = provider.find_by_exact_title(title)
    if existing is not None:
        return {
            "document_id": existing["id"],
            "document_url": existing["url"],
            "revision_id": None,
            "summary": f"Document '{title}' already exists; returned existing document",
            "created": False,
        }
    result = create_document(db, tool_ctx, title, markdown, theme_name)
    result["created"] = True
    return result


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


def rename_document(db: Session, tool_ctx, document_id: str, title: str) -> dict:
    provider = _provider(db, tool_ctx)
    _get_or_not_found(provider, document_id)
    provider.rename_file(document_id, title)
    return {
        "document_id": document_id,
        "document_url": DOC_URL.format(document_id),
        "summary": f"Renamed document to '{title}'",
    }


def restore_document(db: Session, tool_ctx, document_id: str) -> dict:
    provider = _provider(db, tool_ctx)
    try:
        provider.untrash_document(document_id)
    except DocNotFound:
        raise tool_errors.doc_not_found(document_id) from None
    return {
        "document_id": document_id,
        "document_url": DOC_URL.format(document_id),
        "summary": "Document restored from Google Drive trash",
    }


def create_folder(db: Session, tool_ctx, name: str, parent_id: str | None) -> dict:
    provider = _provider(db, tool_ctx)
    try:
        folder = provider.create_folder(name, parent_id)
    except DocNotFound:
        raise tool_errors.doc_not_found(parent_id or "") from None
    return {
        "folder_id": folder["id"],
        "name": folder["name"],
        "url": FOLDER_URL.format(folder["id"]),
    }


def list_folders(db: Session, tool_ctx, limit: int = 100) -> list[dict]:
    return _provider(db, tool_ctx).list_folders(limit)


def delete_folder(db: Session, tool_ctx, folder_id: str, confirm: bool) -> dict:
    if not confirm:
        raise tool_errors.confirmation_required(
            {
                "operation": "delete_folder",
                "folder_id": folder_id,
                "effect": (
                    "Moves the folder to Google Drive trash. Files inside are NOT "
                    "trashed (Drive keeps them in My Drive)."
                ),
            }
        )
    provider = _provider(db, tool_ctx)
    try:
        provider.trash_document(folder_id)
    except DocNotFound:
        raise tool_errors.doc_not_found(folder_id) from None
    return {
        "document_id": folder_id,
        "document_url": FOLDER_URL.format(folder_id),
        "summary": "Folder moved to Google Drive trash",
    }


def move_document(
    db: Session, tool_ctx, document_id: str, folder_id: str, remove_current: bool = True
) -> dict:
    provider = _provider(db, tool_ctx)
    _get_or_not_found(provider, document_id)
    try:
        provider.move_file(document_id, folder_id, remove_current)
    except DocNotFound:
        raise tool_errors.doc_not_found(folder_id) from None
    return {
        "document_id": document_id,
        "document_url": DOC_URL.format(document_id),
        "summary": f"Moved document into folder {folder_id}",
    }


def copy_document(db: Session, tool_ctx, document_id: str, title: str | None) -> dict:
    provider = _provider(db, tool_ctx)
    _get_or_not_found(provider, document_id)
    try:
        copied = provider.copy_document(document_id, title)
    except DocNotFound:
        raise tool_errors.doc_not_found(document_id) from None
    return {
        "document_id": copied["id"],
        "document_url": copied["url"],
        "summary": f"Copied document as '{copied['name']}'",
    }


def export_document(db: Session, tool_ctx, document_id: str, file_format: str) -> dict:
    if file_format != "pdf":
        raise tool_errors.invalid_request("format must be 'pdf'")
    provider = _provider(db, tool_ctx)
    _get_or_not_found(provider, document_id)
    try:
        data = provider.export_file(document_id, "application/pdf")
    except ExportTooLarge:
        raise tool_errors.invalid_request(
            "Export too large: exceeds Google's 10MB export limit"
        ) from None
    except DocNotFound:
        raise tool_errors.doc_not_found(document_id) from None
    if len(data) > EXPORT_SIZE_LIMIT:
        raise tool_errors.invalid_request(
            f"Export too large ({len(data)} bytes); limit is {EXPORT_SIZE_LIMIT} bytes"
        )
    return {
        "format": "pdf",
        "mime": "application/pdf",
        "size_bytes": len(data),
        "content_base64": base64.b64encode(data).decode("ascii"),
        "filename": "document.pdf",
    }


def share_document(
    db: Session, tool_ctx, document_id: str, email: str, role: str, notify: bool = True
) -> dict:
    provider = _provider(db, tool_ctx)
    _get_or_not_found(provider, document_id)
    try:
        permission = provider.share_file(document_id, email, role, notify)
    except DocNotFound:
        raise tool_errors.doc_not_found(document_id) from None
    return {
        "document_id": document_id,
        "permission_id": permission["id"],
        "email": email,
        "role": role,
        "summary": f"Shared document with {email} as {role}",
    }


def list_permissions(db: Session, tool_ctx, document_id: str) -> list[dict]:
    provider = _provider(db, tool_ctx)
    _get_or_not_found(provider, document_id)
    return provider.list_permissions(document_id)


def list_document_comments(
    db: Session, tool_ctx, document_id: str, limit: int = 50
) -> list[dict]:
    provider = _provider(db, tool_ctx)
    _get_or_not_found(provider, document_id)
    try:
        return provider.list_comments(document_id, limit)
    except DocNotFound:
        raise tool_errors.doc_not_found(document_id) from None


def add_document_comment(db: Session, tool_ctx, document_id: str, content: str) -> dict:
    provider = _provider(db, tool_ctx)
    _get_or_not_found(provider, document_id)
    try:
        comment = provider.add_comment(document_id, content)
    except DocNotFound:
        raise tool_errors.doc_not_found(document_id) from None
    return {
        "document_id": document_id,
        "comment_id": comment["id"],
        "summary": "Comment added to document",
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
