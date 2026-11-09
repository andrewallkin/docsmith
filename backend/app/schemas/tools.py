from typing import Literal

from pydantic import BaseModel, EmailStr, Field

# ---- documents ----


class DocumentSearchRequest(BaseModel):
    query: str = Field(default="", max_length=200)
    full_text: str | None = Field(default=None, max_length=200)
    folder_id: str | None = Field(default=None, max_length=300)
    limit: int = Field(default=10, ge=1, le=50)


class DocumentSummary(BaseModel):
    id: str
    name: str
    url: str
    modified_time: str


class DocumentSearchResponse(BaseModel):
    documents: list[DocumentSummary]


class OutlineEntryOut(BaseModel):
    heading: str
    level: int
    start_index: int


class DocumentReadResponse(BaseModel):
    id: str
    title: str
    url: str
    revision_id: str
    markdown: str
    outline: list[OutlineEntryOut]


class DocumentCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    markdown: str = Field(min_length=1, max_length=1_000_000)
    theme: str | None = None
    folder_id: str | None = Field(default=None, max_length=300)


class DocumentGetOrCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    markdown: str = Field(min_length=1, max_length=1_000_000)
    theme: str | None = None


class DocumentUpdateRequest(BaseModel):
    mode: str  # "replace_section" | "regenerate"
    section_heading: str | None = Field(default=None, max_length=300)
    markdown: str | None = Field(default=None, min_length=1, max_length=1_000_000)
    theme: str | None = None
    required_revision_id: str | None = None
    confirm: bool = False


class DocumentAppendRequest(BaseModel):
    markdown: str = Field(min_length=1, max_length=1_000_000)
    required_revision_id: str | None = None


class DocumentDeleteRequest(BaseModel):
    confirm: bool


class DocumentRenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)


class DocumentCopyRequest(BaseModel):
    title: str | None = Field(default=None, max_length=300)


class ExportResponse(BaseModel):
    format: str
    mime: str
    size_bytes: int
    content_base64: str
    filename: str


class FolderCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    parent_id: str | None = Field(default=None, max_length=300)


class FolderCreateResponse(BaseModel):
    folder_id: str
    name: str
    url: str


class FolderOut(BaseModel):
    id: str
    name: str
    parents: list[str] = []


class FolderListResponse(BaseModel):
    folders: list[FolderOut]


class FolderDeleteRequest(BaseModel):
    confirm: bool = False


class DocumentMoveRequest(BaseModel):
    folder_id: str = Field(max_length=300)
    remove_current: bool = True


class MutationResponse(BaseModel):
    document_id: str
    document_url: str
    revision_id: str | None = None
    summary: str
    created: bool = False


class MutationPreviewResponse(BaseModel):
    detail: str
    preview: dict


# ---- sharing ----


class ShareRequest(BaseModel):
    email: EmailStr
    role: Literal["reader", "writer", "commenter"]
    notify: bool = True


class ShareResponse(BaseModel):
    document_id: str
    permission_id: str
    email: str
    role: str
    summary: str


class PermissionOut(BaseModel):
    id: str
    email: str
    role: str
    type: str


class ShareListResponse(BaseModel):
    permissions: list[PermissionOut]


# ---- comments ----


class CommentCreateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=5000)


class CommentResponse(BaseModel):
    document_id: str
    comment_id: str
    summary: str


class CommentOut(BaseModel):
    id: str
    author: str
    content: str
    created_time: str
    resolved: bool


class CommentListResponse(BaseModel):
    comments: list[CommentOut]


# ---- spreadsheets ----


class SpreadsheetCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    sheets: list[str] | None = None  # <=50 names, each 1-100 chars (service validates)
    folder_id: str | None = Field(default=None, max_length=300)


class SpreadsheetOut(BaseModel):
    id: str
    name: str
    url: str
    modified_time: str


class SpreadsheetSearchRequest(BaseModel):
    query: str = Field(default="", max_length=200)
    folder_id: str | None = Field(default=None, max_length=300)
    limit: int = Field(default=10, ge=1, le=50)


class SpreadsheetSearchResponse(BaseModel):
    spreadsheets: list[SpreadsheetOut]


class SheetInfo(BaseModel):
    name: str
    sheet_id: int
    rows: int
    columns: int
    hidden: bool


class SpreadsheetMetaResponse(BaseModel):
    spreadsheet_id: str
    title: str
    url: str
    modified_time: str
    sheets: list[SheetInfo]


class SpreadsheetMutationResponse(BaseModel):
    spreadsheet_id: str
    url: str | None = None
    summary: str


class SpreadsheetCreateResponse(SpreadsheetMutationResponse):
    sheets: list[str]


class ValuesWriteRequest(BaseModel):
    sheet: str = Field(min_length=1, max_length=100)
    range: str = Field(min_length=1, max_length=200)
    rows: list[list[str | int | float | bool | None]]
    mode: Literal["raw", "user_entered"] = "user_entered"
    confirm: bool = False


class ValuesAppendRequest(BaseModel):
    sheet: str = Field(min_length=1, max_length=100)
    rows: list[list[str | int | float | bool | None]]
    confirm: bool = False


class ValuesClearRequest(BaseModel):
    sheet: str = Field(min_length=1, max_length=100)
    range: str = Field(min_length=1, max_length=200)
    confirm: bool = False


class FindReplaceRequest(BaseModel):
    find: str = Field(min_length=1, max_length=500)
    replace: str = Field(default="", max_length=500)
    sheet: str | None = Field(default=None, max_length=100)
    match_case: bool = False
    whole_cell: bool = False
    confirm: bool = False


class ValuesWriteResponse(SpreadsheetMutationResponse):
    sheet: str
    range: str
    updated_cells: int


class ValuesAppendResponse(SpreadsheetMutationResponse):
    sheet: str
    range: str
    updated_rows: int


class ValuesClearResponse(SpreadsheetMutationResponse):
    sheet: str
    range: str


class FindReplaceResponse(SpreadsheetMutationResponse):
    occurrences_changed: int


class ValuesReadResponse(BaseModel):
    spreadsheet_id: str
    title: str
    sheet: str
    range: str
    mode: str
    format: str
    values: list[list[str | int | float | bool | None]]
    formulas: list[list[str | int | float | bool | None]] | None = None
    csv: str | None = None
    markdown: str | None = None


class SheetAddRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    rows: int = Field(default=1000, ge=1, le=50_000)
    cols: int = Field(default=26, ge=1, le=500)


class SheetUpdateRequest(BaseModel):
    new_name: str | None = Field(default=None, min_length=1, max_length=100)
    hidden: bool | None = None


class SheetDeleteRequest(BaseModel):
    confirm: bool = False


class SheetMutationResponse(SpreadsheetMutationResponse):
    sheet: str


class SpreadsheetRenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)


class SpreadsheetMoveRequest(BaseModel):
    folder_id: str = Field(max_length=300)
    remove_current: bool = True


class SpreadsheetCopyRequest(BaseModel):
    title: str | None = Field(default=None, max_length=300)


class SpreadsheetDeleteRequest(BaseModel):
    confirm: bool = False


class SpreadsheetShareResponse(BaseModel):
    spreadsheet_id: str
    permission_id: str
    email: str
    role: str
    summary: str


class ReauthUrlResponse(BaseModel):
    reauth_required: bool
    reauth_url: str
