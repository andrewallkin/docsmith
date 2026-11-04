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
