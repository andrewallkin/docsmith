from pydantic import BaseModel, Field


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class ApiKeyOut(BaseModel):
    id: str
    name: str
    key_prefix: str
    created_at: str
    last_used_at: str | None
    revoked_at: str | None


class ApiKeyCreated(BaseModel):
    key: ApiKeyOut
    # Plaintext, shown exactly once at creation.
    full_key: str
