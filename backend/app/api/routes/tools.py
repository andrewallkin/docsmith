from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import PlainTextResponse

from app.api.deps import ToolAuthContext, get_tool_auth_context
from app.schemas.tools import (
    DocumentAppendRequest,
    DocumentCopyRequest,
    DocumentCreateRequest,
    DocumentDeleteRequest,
    DocumentGetOrCreateRequest,
    DocumentMoveRequest,
    DocumentReadResponse,
    DocumentRenameRequest,
    DocumentSearchRequest,
    DocumentSearchResponse,
    DocumentSummary,
    DocumentUpdateRequest,
    ExportResponse,
    FolderCreateRequest,
    FolderCreateResponse,
    FolderDeleteRequest,
    FolderListResponse,
    FolderOut,
    MutationResponse,
)
from app.services import document_service

router = APIRouter(prefix="/v1/tools", tags=["tools"])

TOOL_DOCS = """# Docsmith tool API

Base URL: `{base}/api/v1` — all requests: `Authorization: Bearer ds_live_...`

| Method | Path | Purpose |
|---|---|---|
| POST | /api/v1/tools/documents/search | Search accessible docs by title |
| GET | /api/v1/tools/documents/{id} | Read content (markdown) + outline |
| POST | /api/v1/tools/documents | Create a Google Doc from markdown |
| PATCH | /api/v1/tools/documents/{id} | Update: replace_section or regenerate |
| POST | /api/v1/tools/documents/{id}/append | Append markdown at end of doc |
| POST | /api/v1/tools/documents/{id}/delete | Move doc to trash (requires confirm: true) |
| POST | /api/v1/tools/documents/{id}/restore | Restore doc from trash |
| POST | /api/v1/tools/documents/{id}/rename | Rename a doc |
| POST | /api/v1/tools/documents/{id}/move | Move doc into a folder |
| POST | /api/v1/tools/documents/{id}/copy | Copy a doc (optional new title) |
| GET | /api/v1/tools/documents/{id}/export | Export doc as PDF (base64, query format=pdf) |
| POST | /api/v1/tools/folders | Create a Drive folder (optional parent_id) |
| GET | /api/v1/tools/folders | List accessible Drive folders |
| POST | /api/v1/tools/folders/{id}/delete | Move folder to trash (requires confirm: true) |

Markdown supports: H1-H3 headings, bold/italic, links, ordered and unordered
lists, tables, `---` page breaks. Optional theme: `professional` (default).

Only app-created and Picker-granted docs are accessible (drive.file scope).
Mutating calls accept `required_revision_id`; stale ids return 409.
Destructive operations (regenerate, delete) require `confirm: true` or
return 422 with a preview.
"""


@router.get("/docs", response_class=PlainTextResponse)
def tool_docs():
    from app.config import get_settings

    base = get_settings().frontend_url
    # TOOL_DOCS contains literal {id} placeholders; str.format would break on them
    return PlainTextResponse(TOOL_DOCS.replace("{base}", base), media_type="text/markdown")


@router.post("/documents/search", response_model=DocumentSearchResponse)
def search_documents(
    payload: DocumentSearchRequest,
    tool_ctx: ToolAuthContext = Depends(get_tool_auth_context),
):
    results = document_service.search_documents(
        tool_ctx.db,
        tool_ctx,
        payload.query,
        payload.limit,
        full_text=payload.full_text,
        folder_id=payload.folder_id,
    )
    return DocumentSearchResponse(
        documents=[DocumentSummary(**doc) for doc in results]
    )


@router.get("/documents/{document_id}", response_model=DocumentReadResponse)
def read_document(
    document_id: str,
    tool_ctx: ToolAuthContext = Depends(get_tool_auth_context),
):
    result = document_service.read_document(tool_ctx.db, tool_ctx, document_id)
    return DocumentReadResponse(**result)


@router.post("/documents", response_model=MutationResponse, status_code=201)
def create_document(
    payload: DocumentCreateRequest,
    tool_ctx: ToolAuthContext = Depends(get_tool_auth_context),
):
    result = document_service.create_document(
        tool_ctx.db, tool_ctx, payload.title, payload.markdown, payload.theme
    )
    return MutationResponse(**result)


@router.post("/documents/get-or-create", response_model=MutationResponse)
def get_or_create_document(
    payload: DocumentGetOrCreateRequest,
    response: Response,
    tool_ctx: ToolAuthContext = Depends(get_tool_auth_context),
):
    result = document_service.get_or_create_document(
        tool_ctx.db, tool_ctx, payload.title, payload.markdown, payload.theme
    )
    response.status_code = 201 if result["created"] else 200
    return MutationResponse(**result)


@router.patch("/documents/{document_id}", response_model=MutationResponse)
def update_document(
    document_id: str,
    payload: DocumentUpdateRequest,
    tool_ctx: ToolAuthContext = Depends(get_tool_auth_context),
):
    if payload.mode == "replace_section":
        if not payload.section_heading or not payload.markdown:
            from app.core import tool_errors

            raise tool_errors.invalid_request(
                "replace_section requires section_heading and markdown"
            )
        result = document_service.replace_section(
            tool_ctx.db,
            tool_ctx,
            document_id,
            payload.section_heading,
            payload.markdown,
            payload.theme,
            payload.required_revision_id,
        )
        return MutationResponse(**result)
    if payload.mode == "regenerate":
        if not payload.markdown:
            from app.core import tool_errors

            raise tool_errors.invalid_request("regenerate requires markdown")
        result = document_service.regenerate_document(
            tool_ctx.db,
            tool_ctx,
            document_id,
            payload.markdown,
            payload.theme,
            payload.required_revision_id,
            payload.confirm,
        )
        return MutationResponse(**result)
    from app.core import tool_errors

    raise tool_errors.invalid_request("mode must be 'replace_section' or 'regenerate'")


@router.post("/documents/{document_id}/append", response_model=MutationResponse)
def append_document(
    document_id: str,
    payload: DocumentAppendRequest,
    tool_ctx: ToolAuthContext = Depends(get_tool_auth_context),
):
    result = document_service.append_markdown(
        tool_ctx.db, tool_ctx, document_id, payload.markdown, payload.required_revision_id
    )
    return MutationResponse(**result)


@router.post("/documents/{document_id}/delete", response_model=MutationResponse)
def delete_document(
    document_id: str,
    payload: DocumentDeleteRequest,
    tool_ctx: ToolAuthContext = Depends(get_tool_auth_context),
):
    result = document_service.delete_document(tool_ctx.db, tool_ctx, document_id, payload.confirm)
    return MutationResponse(**result)


@router.post("/documents/{document_id}/rename", response_model=MutationResponse)
def rename_document(
    document_id: str,
    payload: DocumentRenameRequest,
    tool_ctx: ToolAuthContext = Depends(get_tool_auth_context),
):
    result = document_service.rename_document(
        tool_ctx.db, tool_ctx, document_id, payload.title
    )
    return MutationResponse(**result)


@router.post("/documents/{document_id}/restore", response_model=MutationResponse)
def restore_document(
    document_id: str,
    tool_ctx: ToolAuthContext = Depends(get_tool_auth_context),
):
    result = document_service.restore_document(tool_ctx.db, tool_ctx, document_id)
    return MutationResponse(**result)


@router.post("/folders", response_model=FolderCreateResponse, status_code=201)
def create_folder(
    payload: FolderCreateRequest,
    tool_ctx: ToolAuthContext = Depends(get_tool_auth_context),
):
    result = document_service.create_folder(tool_ctx.db, tool_ctx, payload.name, payload.parent_id)
    return FolderCreateResponse(**result)


@router.get("/folders", response_model=FolderListResponse)
def list_folders(
    tool_ctx: ToolAuthContext = Depends(get_tool_auth_context),
):
    folders = document_service.list_folders(tool_ctx.db, tool_ctx)
    return FolderListResponse(folders=[FolderOut(**folder) for folder in folders])


@router.post("/folders/{folder_id}/delete", response_model=MutationResponse)
def delete_folder(
    folder_id: str,
    payload: FolderDeleteRequest,
    tool_ctx: ToolAuthContext = Depends(get_tool_auth_context),
):
    result = document_service.delete_folder(tool_ctx.db, tool_ctx, folder_id, payload.confirm)
    return MutationResponse(**result)


@router.post("/documents/{document_id}/move", response_model=MutationResponse)
def move_document(
    document_id: str,
    payload: DocumentMoveRequest,
    tool_ctx: ToolAuthContext = Depends(get_tool_auth_context),
):
    result = document_service.move_document(
        tool_ctx.db, tool_ctx, document_id, payload.folder_id, payload.remove_current
    )
    return MutationResponse(**result)


@router.post("/documents/{document_id}/copy", response_model=MutationResponse, status_code=201)
def copy_document(
    document_id: str,
    payload: DocumentCopyRequest,
    tool_ctx: ToolAuthContext = Depends(get_tool_auth_context),
):
    result = document_service.copy_document(
        tool_ctx.db, tool_ctx, document_id, payload.title
    )
    return MutationResponse(**result)


@router.get("/documents/{document_id}/export", response_model=ExportResponse)
def export_document(
    document_id: str,
    format: str = Query(default="pdf"),
    tool_ctx: ToolAuthContext = Depends(get_tool_auth_context),
):
    result = document_service.export_document(tool_ctx.db, tool_ctx, document_id, format)
    return ExportResponse(**result)
