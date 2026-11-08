from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import PlainTextResponse

from app.api.deps import ToolAuthContext, get_tool_auth_context
from app.schemas.tools import (
    CommentCreateRequest,
    CommentListResponse,
    CommentOut,
    CommentResponse,
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
    FindReplaceRequest,
    FindReplaceResponse,
    FolderCreateRequest,
    FolderCreateResponse,
    FolderDeleteRequest,
    FolderListResponse,
    FolderOut,
    MutationResponse,
    PermissionOut,
    ShareListResponse,
    ShareRequest,
    ShareResponse,
    SheetAddRequest,
    SheetDeleteRequest,
    SheetMutationResponse,
    SheetUpdateRequest,
    SpreadsheetCopyRequest,
    SpreadsheetCreateRequest,
    SpreadsheetCreateResponse,
    SpreadsheetDeleteRequest,
    SpreadsheetMetaResponse,
    SpreadsheetMoveRequest,
    SpreadsheetMutationResponse,
    SpreadsheetOut,
    SpreadsheetRenameRequest,
    SpreadsheetSearchRequest,
    SpreadsheetSearchResponse,
    SpreadsheetShareResponse,
    ValuesAppendRequest,
    ValuesAppendResponse,
    ValuesClearRequest,
    ValuesClearResponse,
    ValuesReadResponse,
    ValuesWriteRequest,
    ValuesWriteResponse,
)
from app.services import document_service, sheet_service

router = APIRouter(prefix="/v1/tools", tags=["tools"])

TOOL_DOCS = """# Docsmith tool API

Base URL: `{base}/api/v1` — all requests: `Authorization: Bearer ds_live_...`

| Method | Path | Purpose |
|---|---|---|
| POST | /api/v1/tools/documents/search | Search (query, full_text, folder_id; empty lists all) |
| GET | /api/v1/tools/documents/{id} | Read content (markdown) + outline |
| POST | /api/v1/tools/documents | Create a Google Doc from markdown (optional folder_id) |
| POST | /api/v1/tools/documents/get-or-create | Create a doc, or return existing with same title |
| PATCH | /api/v1/tools/documents/{id} | Update: replace_section or regenerate |
| POST | /api/v1/tools/documents/{id}/append | Append markdown at end of doc |
| POST | /api/v1/tools/documents/{id}/delete | Move doc to trash (requires confirm: true) |
| POST | /api/v1/tools/documents/{id}/restore | Restore doc from trash |
| POST | /api/v1/tools/documents/{id}/rename | Rename a doc |
| POST | /api/v1/tools/documents/{id}/move | Move doc into a folder |
| POST | /api/v1/tools/documents/{id}/copy | Copy a doc (optional new title) |
| POST | /api/v1/tools/documents/{id}/share | Share doc with an email (reader/writer/commenter) |
| GET | /api/v1/tools/documents/{id}/share | List sharing permissions on a doc |
| GET | /api/v1/tools/documents/{id}/comments | List comments on a doc |
| POST | /api/v1/tools/documents/{id}/comments | Add a comment to a doc |
| GET | /api/v1/tools/documents/{id}/export | Export doc as PDF (base64, query format=pdf) |
| POST | /api/v1/tools/folders | Create a Drive folder (optional parent_id) |
| GET | /api/v1/tools/folders | List accessible Drive folders |
| POST | /api/v1/tools/folders/{id}/delete | Move folder to trash (requires confirm: true) |

Markdown supports: H1-H3 headings, bold/italic, links, ordered and unordered
lists, tables, `---` page breaks. Optional theme: `professional` (default).

All files in the connected Google Drive are accessible (full drive scope).
Mutating calls accept `required_revision_id`; stale ids return 409.
get-or-create returns 201 on create / 200 on existing (created: true/false).
Rename/move/copy/share/comments/folder ops do not accept required_revision_id
(metadata ops don't change the revision).
Destructive operations (regenerate, delete, folder delete) require
`confirm: true` or return 422 with a preview.

## Spreadsheets

| Method | Path | Purpose |
|---|---|---|
| POST | /api/v1/tools/spreadsheets | Create spreadsheet. `sheets` lists tabs (omit = "Sheet1"). |
| POST | /api/v1/tools/spreadsheets/search | Search spreadsheets (query, folder_id, limit) |
| GET | /api/v1/tools/spreadsheets/{id} | Get metadata: title, url, tabs (name, rows, columns) |
| POST | /api/v1/tools/spreadsheets/{id}/delete | Trash spreadsheet (requires confirm: true) |
| POST | /api/v1/tools/spreadsheets/{id}/restore | Restore spreadsheet from trash |
| POST | /api/v1/tools/spreadsheets/{id}/rename | Rename a spreadsheet |
| POST | /api/v1/tools/spreadsheets/{id}/move | Move spreadsheet into a folder |
| POST | /api/v1/tools/spreadsheets/{id}/copy | Copy a spreadsheet (optional new title) |
| POST | /api/v1/tools/spreadsheets/{id}/share | Share spreadsheet (reader/writer/commenter) |
| GET | /api/v1/tools/spreadsheets/{id}/share | List sharing permissions |
| GET | /api/v1/tools/spreadsheets/{id}/export?format=csv\|xlsx\|pdf | Export as base64 |
| GET | /api/v1/tools/spreadsheets/{id}/values | Read values (query: sheet, range, mode, format) |
| POST | /api/v1/tools/spreadsheets/{id}/values | Write rows to a range |
| POST | /api/v1/tools/spreadsheets/{id}/values/append | Append rows to a tab |
| POST | /api/v1/tools/spreadsheets/{id}/values/clear | Clear a range (requires confirm: true) |
| POST | /api/v1/tools/spreadsheets/{id}/find-replace | Find and replace (requires confirm: true) |
| POST | /api/v1/tools/spreadsheets/{id}/sheets | Add a tab (name, rows, cols) |
| PATCH | /api/v1/tools/spreadsheets/{id}/sheets/{name} | Rename or hide/unhide a tab |
| DELETE | /api/v1/tools/spreadsheets/{id}/sheets/{name} | Delete a tab (requires confirm: true) |

A1 notation: `Sheet1!A1:D10` is a rectangle, `A:D` is a whole column, and a
whole tab is read by omitting `range`. Writes require an explicit bounded
range like A1:D10 or a single cell. Formulas: send `{"sheet": "Sheet1",
"range": "F2", "rows": [["=SUM(B2:B10)"]]}` — the default `mode` is
`user_entered`, so Google recalculates the formula on write.

Destructive spreadsheet operations require `confirm: true` (otherwise 422
with a preview, nothing changed): values/clear, find-replace, delete tab
(DELETE .../sheets/{name}), delete spreadsheet.

Writes beyond the current grid grow it automatically; reads beyond it fail.
Reads are capped at 200,000 cells per request — page large tabs with the
`range` parameter.

Folder endpoints (/folders) and the move/copy/rename/share operations above
work identically for spreadsheets. Creating a doc or a spreadsheet accepts
an optional `folder_id` to file it immediately.

All examples below use `curl` with `Authorization: Bearer $KEY` (replace
`$KEY` with your `ds_live_...` key and `$BASE` with the base URL). JSON
responses include a `summary` field describing what happened.

## Examples — Documents

### Example — search docs
```bash
curl -X POST $BASE/api/v1/tools/documents/search \\
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \\
  -d '{"query": "weekly report", "limit": 10}'
```

### Example — read a doc
```bash
curl $BASE/api/v1/tools/documents/$DOC_ID \\
  -H "Authorization: Bearer $KEY"
```

### Example — create a doc from markdown
```bash
curl -X POST $BASE/api/v1/tools/documents \\
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \\
  -d '{"title": "Weekly report", "markdown": "# Weekly report\\n\\nAll good."}'
```

### Example — create or return existing by title
```bash
curl -X POST $BASE/api/v1/tools/documents/get-or-create \\
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \\
  -d '{"title": "Weekly report", "markdown": "# Weekly report"}'
```

### Example — replace a section
```bash
curl -X PATCH $BASE/api/v1/tools/documents/$DOC_ID \\
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \\
  -d '{"update": {"replace_section": {"heading": "Weekly report",
    "markdown": "# Weekly report\\n\\nUpdated text."}}}'
```

### Example — append markdown
```bash
curl -X POST $BASE/api/v1/tools/documents/$DOC_ID/append \\
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \\
  -d '{"markdown": "## New section\\n\\nAppended at the end."}'
```

### Example — move doc to trash
```bash
curl -X POST $BASE/api/v1/tools/documents/$DOC_ID/delete \\
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \\
  -d '{"confirm": true}'
```

### Example — restore from trash
```bash
curl -X POST $BASE/api/v1/tools/documents/$DOC_ID/restore \\
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -d '{}'
```

### Example — rename a doc
```bash
curl -X POST $BASE/api/v1/tools/documents/$DOC_ID/rename \\
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \\
  -d '{"name": "Q3 Weekly report"}'
```

### Example — move doc into a folder
```bash
curl -X POST $BASE/api/v1/tools/documents/$DOC_ID/move \\
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \\
  -d '{"folder_id": "folder123"}'
```

### Example — copy a doc
```bash
curl -X POST $BASE/api/v1/tools/documents/$DOC_ID/copy \\
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \\
  -d '{"title": "Weekly report (copy)"}'
```

### Example — share with an email
```bash
curl -X POST $BASE/api/v1/tools/documents/$DOC_ID/share \\
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \\
  -d '{"email": "person@example.com", "role": "writer"}'
```

### Example — list sharing permissions
```bash
curl $BASE/api/v1/tools/documents/$DOC_ID/share \\
  -H "Authorization: Bearer $KEY"
```

### Example — list comments
```bash
curl $BASE/api/v1/tools/documents/$DOC_ID/comments \\
  -H "Authorization: Bearer $KEY"
```

### Example — add a comment
```bash
curl -X POST $BASE/api/v1/tools/documents/$DOC_ID/comments \\
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \\
  -d '{"comment": "Please review section 2."}'
```

### Example — export as PDF
```bash
curl "$BASE/api/v1/tools/documents/$DOC_ID/export?format=pdf" \\
  -H "Authorization: Bearer $KEY"
```

## Examples — Spreadsheets

### Example — create a spreadsheet
```bash
curl -X POST $BASE/api/v1/tools/spreadsheets \\
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \\
  -d '{"title": "Budget", "sheets": ["Data", "Summary"]}'
```
`Sheets` lists the tabs to create (omit for one "Sheet1"). Response returns
`spreadsheet_id` and `url`.

### Example — search spreadsheets
```bash
curl -X POST $BASE/api/v1/tools/spreadsheets/search \\
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \\
  -d '{"query": "Budget"}'
```

### Example — get metadata (tabs, grid sizes)
```bash
curl $BASE/api/v1/tools/spreadsheets/$SPREADSHEET_ID \\
  -H "Authorization: Bearer $KEY"
```

### Example — write rows with headings
```bash
curl -X POST $BASE/api/v1/tools/spreadsheets/$SPREADSHEET_ID/values \\
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \\
  -d '{"sheet": "Data", "range": "A1",
    "rows": [["Item", "Qty", "Price"], ["Apple", 4, 1.5], ["Bread", 2, 2.25]]}'
```

### Example — write a formula (user_entered recalculates)
```bash
curl -X POST $BASE/api/v1/tools/spreadsheets/$SPREADSHEET_ID/values \\
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \\
  -d '{"sheet": "Data", "range": "D2", "rows": [["=SUM(B2:B10)"]]}'
```

### Example — read values
```bash
curl "$BASE/api/v1/tools/spreadsheets/$SPREADSHEET_ID/values?sheet=Data&range=A1:D10&mode=values" \\
  -H "Authorization: Bearer $KEY"
```
`mode` is `values` (computed), `formulas` (formula strings), or `both`.
`format` is `json`, `csv`, or `markdown`. Omit `range` to read the whole tab.

### Example — append rows
```bash
curl -X POST $BASE/api/v1/tools/spreadsheets/$SPREADSHEET_ID/values/append \\
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \\
  -d '{"sheet": "Data", "rows": [["Milk", 3, 1.1]]}'
```

### Example — clear a range
```bash
curl -X POST $BASE/api/v1/tools/spreadsheets/$SPREADSHEET_ID/values/clear \\
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \\
  -d '{"sheet": "Data", "range": "A2:D10", "confirm": true}'
```

### Example — find and replace
```bash
curl -X POST $BASE/api/v1/tools/spreadsheets/$SPREADSHEET_ID/find-replace \\
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \\
  -d '{"find": "Fruit", "replace": "Produce", "confirm": true}'
```
Omit `sheet` to search all tabs.

### Example — add a tab
```bash
curl -X POST $BASE/api/v1/tools/spreadsheets/$SPREADSHEET_ID/sheets \\
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \\
  -d '{"name": "Archive", "rows": 500, "columns": 10}'
```

### Example — rename or hide a tab
```bash
curl -X PATCH $BASE/api/v1/tools/spreadsheets/$SPREADSHEET_ID/sheets/Data \\
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \\
  -d '{"name": "Inputs", "hidden": false}'
```

### Example — delete a tab
```bash
curl -X DELETE $BASE/api/v1/tools/spreadsheets/$SPREADSHEET_ID/sheets/Archive \\
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \\
  -d '{"confirm": true}'
```

### Example — trash a spreadsheet
```bash
curl -X POST $BASE/api/v1/tools/spreadsheets/$SPREADSHEET_ID/delete \\
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \\
  -d '{"confirm": true}'
```

### Example — restore from trash
```bash
curl -X POST $BASE/api/v1/tools/spreadsheets/$SPREADSHEET_ID/restore \\
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -d '{}'
```

### Example — rename / move / copy
```bash
curl -X POST $BASE/api/v1/tools/spreadsheets/$SPREADSHEET_ID/rename \\
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \\
  -d '{"name": "Budget 2026"}'
```
`move` takes `{"folder_id": "..."}`, `copy` optionally takes `{"title": "..."}`.

### Example — share a spreadsheet
```bash
curl -X POST $BASE/api/v1/tools/spreadsheets/$SPREADSHEET_ID/share \\
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \\
  -d '{"email": "person@example.com", "role": "reader"}'
```

### Example — export csv / xlsx / pdf
```bash
curl "$BASE/api/v1/tools/spreadsheets/$SPREADSHEET_ID/export?format=xlsx" \\
  -H "Authorization: Bearer $KEY"
```

## Examples — Folders

### Example — create a folder
```bash
curl -X POST $BASE/api/v1/tools/folders \\
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \\
  -d '{"name": "Reports"}'
```

### Example — list folders
```bash
curl $BASE/api/v1/tools/folders \\
  -H "Authorization: Bearer $KEY"
```

### Example — move folder to trash
```bash
curl -X POST $BASE/api/v1/tools/folders/$FOLDER_ID/delete \\
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \\
  -d '{"confirm": true}'
```
"""


@router.get("/docs", response_class=PlainTextResponse)
def tool_docs():
    from app.config import get_settings

    base = get_settings().frontend_url
    # TOOL_DOCS contains literal {id} placeholders; str.format would break on them
    return PlainTextResponse(TOOL_DOCS.replace("{base}", base), media_type="text/markdown")


TOOL_INFO = """Docsmith tool API — quick start for bots.

Auth: every request needs `Authorization: Bearer ds_live_...` (create a key
in the Docsmith dashboard). All paths are relative to `{base}/api/v1`.

Full endpoint reference: GET {base}/api/v1/tools/docs (no auth, markdown).

Core operations:
- POST tools/documents/search — search/list docs
- GET tools/documents/{{id}} — read a doc as markdown
- POST tools/documents — create a doc from markdown (optional folder_id)
- POST tools/documents/get-or-create — create or return existing by title
- PATCH tools/documents/{{id}} — replace a section or regenerate
- POST tools/documents/{{id}}/append — append markdown
- POST tools/documents/{{id}}/rename | /move | /copy | /share — organize
- GET/POST tools/documents/{{id}}/comments — read/add comments
- POST tools/documents/{{id}}/export?format=pdf — export as base64 PDF
- POST/GET tools/folders — create/list Drive folders
- tools/spreadsheets — CRUD on Google Sheets: create/search, values
  read/write/append, tabs, export, share (writes auto-grow the grid)
- Destructive ops (delete, regenerate, folder delete) need `confirm: true`;
  so do spreadsheet values/clear, find-replace, delete tab, delete
  spreadsheet.
"""


@router.get("/info", response_class=PlainTextResponse)
def tool_info():
    from app.config import get_settings

    base = get_settings().frontend_url
    return PlainTextResponse(TOOL_INFO.replace("{base}", base), media_type="text/plain")


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
        tool_ctx.db, tool_ctx, payload.title, payload.markdown, payload.theme,
        payload.folder_id,
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


@router.post("/documents/{document_id}/share", response_model=ShareResponse)
def share_document(
    document_id: str,
    payload: ShareRequest,
    tool_ctx: ToolAuthContext = Depends(get_tool_auth_context),
):
    result = document_service.share_document(
        tool_ctx.db, tool_ctx, document_id, payload.email, payload.role, payload.notify
    )
    return ShareResponse(**result)


@router.get("/documents/{document_id}/share", response_model=ShareListResponse)
def list_permissions(
    document_id: str,
    tool_ctx: ToolAuthContext = Depends(get_tool_auth_context),
):
    permissions = document_service.list_permissions(tool_ctx.db, tool_ctx, document_id)
    return ShareListResponse(permissions=[PermissionOut(**p) for p in permissions])


@router.get(
    "/documents/{document_id}/comments", response_model=CommentListResponse
)
def list_comments(
    document_id: str,
    tool_ctx: ToolAuthContext = Depends(get_tool_auth_context),
):
    comments = document_service.list_document_comments(tool_ctx.db, tool_ctx, document_id)
    return CommentListResponse(comments=[CommentOut(**c) for c in comments])


@router.post(
    "/documents/{document_id}/comments",
    response_model=CommentResponse,
    status_code=201,
)
def add_comment(
    document_id: str,
    payload: CommentCreateRequest,
    tool_ctx: ToolAuthContext = Depends(get_tool_auth_context),
):
    result = document_service.add_document_comment(
        tool_ctx.db, tool_ctx, document_id, payload.content
    )
    return CommentResponse(**result)


# ---- spreadsheets ----


@router.post("/spreadsheets", response_model=SpreadsheetCreateResponse, status_code=201)
def create_spreadsheet(
    payload: SpreadsheetCreateRequest,
    tool_ctx: ToolAuthContext = Depends(get_tool_auth_context),
):
    result = sheet_service.create_spreadsheet(
        tool_ctx.db, tool_ctx, payload.title, payload.sheets, payload.folder_id
    )
    return SpreadsheetCreateResponse(
        spreadsheet_id=result["spreadsheet_id"],
        url=result["url"],
        summary=f"Created spreadsheet '{result['title']}'",
        sheets=result["sheets"],
    )


@router.post("/spreadsheets/search", response_model=SpreadsheetSearchResponse)
def search_spreadsheets(
    payload: SpreadsheetSearchRequest,
    tool_ctx: ToolAuthContext = Depends(get_tool_auth_context),
):
    results = sheet_service.search_spreadsheets(
        tool_ctx.db, tool_ctx, payload.query, payload.limit, folder_id=payload.folder_id
    )
    return SpreadsheetSearchResponse(spreadsheets=[SpreadsheetOut(**s) for s in results])


@router.get("/spreadsheets/{spreadsheet_id}", response_model=SpreadsheetMetaResponse)
def get_spreadsheet(
    spreadsheet_id: str,
    tool_ctx: ToolAuthContext = Depends(get_tool_auth_context),
):
    result = sheet_service.get_spreadsheet(tool_ctx.db, tool_ctx, spreadsheet_id)
    return SpreadsheetMetaResponse(**result)


@router.post("/spreadsheets/{spreadsheet_id}/delete", response_model=SpreadsheetMutationResponse)
def delete_spreadsheet(
    spreadsheet_id: str,
    payload: SpreadsheetDeleteRequest | None = None,
    tool_ctx: ToolAuthContext = Depends(get_tool_auth_context),
):
    result = sheet_service.trash_spreadsheet(
        tool_ctx.db, tool_ctx, spreadsheet_id, payload.confirm if payload else False
    )
    return SpreadsheetMutationResponse(**result)


@router.post(
    "/spreadsheets/{spreadsheet_id}/restore", response_model=SpreadsheetMutationResponse
)
def restore_spreadsheet(
    spreadsheet_id: str,
    tool_ctx: ToolAuthContext = Depends(get_tool_auth_context),
):
    result = sheet_service.restore_spreadsheet(tool_ctx.db, tool_ctx, spreadsheet_id)
    return SpreadsheetMutationResponse(**result)


@router.post("/spreadsheets/{spreadsheet_id}/rename", response_model=SpreadsheetMutationResponse)
def rename_spreadsheet(
    spreadsheet_id: str,
    payload: SpreadsheetRenameRequest,
    tool_ctx: ToolAuthContext = Depends(get_tool_auth_context),
):
    result = sheet_service.rename_spreadsheet(tool_ctx.db, tool_ctx, spreadsheet_id, payload.title)
    return SpreadsheetMutationResponse(**result)


@router.post("/spreadsheets/{spreadsheet_id}/move", response_model=SpreadsheetMutationResponse)
def move_spreadsheet(
    spreadsheet_id: str,
    payload: SpreadsheetMoveRequest,
    tool_ctx: ToolAuthContext = Depends(get_tool_auth_context),
):
    result = sheet_service.move_spreadsheet(
        tool_ctx.db, tool_ctx, spreadsheet_id, payload.folder_id, payload.remove_current
    )
    return SpreadsheetMutationResponse(**result)


@router.post(
    "/spreadsheets/{spreadsheet_id}/copy",
    response_model=SpreadsheetMutationResponse,
    status_code=201,
)
def copy_spreadsheet(
    spreadsheet_id: str,
    payload: SpreadsheetCopyRequest,
    tool_ctx: ToolAuthContext = Depends(get_tool_auth_context),
):
    result = sheet_service.copy_spreadsheet(tool_ctx.db, tool_ctx, spreadsheet_id, payload.title)
    return SpreadsheetMutationResponse(**result)


@router.post("/spreadsheets/{spreadsheet_id}/share", response_model=SpreadsheetShareResponse)
def share_spreadsheet(
    spreadsheet_id: str,
    payload: ShareRequest,
    tool_ctx: ToolAuthContext = Depends(get_tool_auth_context),
):
    result = sheet_service.share_spreadsheet(
        tool_ctx.db, tool_ctx, spreadsheet_id, payload.email, payload.role, payload.notify
    )
    return SpreadsheetShareResponse(**result)


@router.get("/spreadsheets/{spreadsheet_id}/share", response_model=ShareListResponse)
def list_spreadsheet_permissions(
    spreadsheet_id: str,
    tool_ctx: ToolAuthContext = Depends(get_tool_auth_context),
):
    permissions = sheet_service.list_spreadsheet_permissions(tool_ctx.db, tool_ctx, spreadsheet_id)
    return ShareListResponse(permissions=[PermissionOut(**p) for p in permissions])


@router.get("/spreadsheets/{spreadsheet_id}/export", response_model=ExportResponse)
def export_spreadsheet(
    spreadsheet_id: str,
    format: str = Query(default="csv"),
    tool_ctx: ToolAuthContext = Depends(get_tool_auth_context),
):
    result = sheet_service.export_spreadsheet(tool_ctx.db, tool_ctx, spreadsheet_id, format)
    return ExportResponse(**result)


@router.get("/spreadsheets/{spreadsheet_id}/values", response_model=ValuesReadResponse)
def read_values(
    spreadsheet_id: str,
    sheet: str | None = None,
    range: str | None = Query(default=None, max_length=200),
    mode: str = Query(default="values"),
    format: str = Query(default="json"),
    tool_ctx: ToolAuthContext = Depends(get_tool_auth_context),
):
    result = sheet_service.read_values(
        tool_ctx.db, tool_ctx, spreadsheet_id, sheet, range, mode, format
    )
    return ValuesReadResponse(**result)


@router.post("/spreadsheets/{spreadsheet_id}/values", response_model=ValuesWriteResponse)
def write_values(
    spreadsheet_id: str,
    payload: ValuesWriteRequest,
    tool_ctx: ToolAuthContext = Depends(get_tool_auth_context),
):
    result = sheet_service.write_values(
        tool_ctx.db,
        tool_ctx,
        spreadsheet_id,
        payload.sheet,
        payload.range,
        payload.rows,
        payload.mode,
        payload.confirm,
    )
    return ValuesWriteResponse(**result)


@router.post(
    "/spreadsheets/{spreadsheet_id}/values/append", response_model=ValuesAppendResponse
)
def append_values(
    spreadsheet_id: str,
    payload: ValuesAppendRequest,
    tool_ctx: ToolAuthContext = Depends(get_tool_auth_context),
):
    result = sheet_service.append_values(
        tool_ctx.db, tool_ctx, spreadsheet_id, payload.sheet, payload.rows, payload.confirm
    )
    return ValuesAppendResponse(**result)


@router.post(
    "/spreadsheets/{spreadsheet_id}/values/clear", response_model=ValuesClearResponse
)
def clear_values(
    spreadsheet_id: str,
    payload: ValuesClearRequest,
    tool_ctx: ToolAuthContext = Depends(get_tool_auth_context),
):
    result = sheet_service.clear_values(
        tool_ctx.db, tool_ctx, spreadsheet_id, payload.sheet, payload.range, payload.confirm
    )
    return ValuesClearResponse(**result)


@router.post("/spreadsheets/{spreadsheet_id}/find-replace", response_model=FindReplaceResponse)
def find_replace(
    spreadsheet_id: str,
    payload: FindReplaceRequest,
    tool_ctx: ToolAuthContext = Depends(get_tool_auth_context),
):
    result = sheet_service.find_replace(
        tool_ctx.db,
        tool_ctx,
        spreadsheet_id,
        payload.find,
        payload.replace,
        sheet=payload.sheet,
        match_case=payload.match_case,
        whole_cell=payload.whole_cell,
        confirm=payload.confirm,
    )
    return FindReplaceResponse(**result)


@router.post("/spreadsheets/{spreadsheet_id}/sheets", response_model=SheetMutationResponse)
def add_sheet(
    spreadsheet_id: str,
    payload: SheetAddRequest,
    tool_ctx: ToolAuthContext = Depends(get_tool_auth_context),
):
    result = sheet_service.add_sheet(
        tool_ctx.db, tool_ctx, spreadsheet_id, payload.name, payload.rows, payload.cols
    )
    return SheetMutationResponse(**result)


@router.patch(
    "/spreadsheets/{spreadsheet_id}/sheets/{sheet_name}", response_model=SheetMutationResponse
)
def update_sheet(
    spreadsheet_id: str,
    sheet_name: str,
    payload: SheetUpdateRequest,
    tool_ctx: ToolAuthContext = Depends(get_tool_auth_context),
):
    result = sheet_service.update_sheet(
        tool_ctx.db, tool_ctx, spreadsheet_id, sheet_name, payload.new_name, payload.hidden
    )
    return SheetMutationResponse(**result)


@router.delete(
    "/spreadsheets/{spreadsheet_id}/sheets/{sheet_name}", response_model=SheetMutationResponse
)
def delete_sheet(
    spreadsheet_id: str,
    sheet_name: str,
    payload: SheetDeleteRequest | None = None,
    tool_ctx: ToolAuthContext = Depends(get_tool_auth_context),
):
    result = sheet_service.delete_sheet(
        tool_ctx.db, tool_ctx, spreadsheet_id, sheet_name, payload.confirm if payload else False
    )
    return SheetMutationResponse(**result)
