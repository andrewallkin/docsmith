from pydantic import BaseModel


class GoogleConnectionStatus(BaseModel):
    connected: bool
    google_email: str | None = None
    scopes: list[str] = []
    connected_at: str | None = None
    expired: bool = False


class GoogleConnectResponse(BaseModel):
    auth_url: str
