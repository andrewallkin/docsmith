from pydantic import BaseModel, Field

# ---- documents ----


class DocumentSearchRequest(BaseModel):
    query: str = Field(default="", max_length=200)
    full_text: str | None = Field(default=None, max_length=200)
    folder_id: str | None = None
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


class MutationResponse(BaseModel):
    document_id: str
    document_url: str
    revision_id: str | None = None
    summary: str
    created: bool = False


class MutationPreviewResponse(BaseModel):
    detail: str
    preview: dict
