import logging
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core import encryption, security
from app.models import GoogleConnection

logger = logging.getLogger(__name__)

SCOPE_DRIVE = "https://www.googleapis.com/auth/drive"
SCOPE_USERINFO_EMAIL = "https://www.googleapis.com/auth/userinfo.email"

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


class GoogleReconnectRequired(Exception):
    """Raised when the stored refresh token is expired or revoked (invalid_grant)."""


def build_auth_url(user_id: uuid.UUID) -> str:
    """Build the Google consent URL. State is a short-lived signed JWT binding the user."""
    state = security.create_token(str(user_id), timedelta(minutes=10))[0]
    settings = get_settings()
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": f"{SCOPE_DRIVE} {SCOPE_USERINFO_EMAIL}",
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    return AUTH_URL + "?" + str(httpx.QueryParams(params))


def exchange_code(code: str) -> dict:
    """Exchange an authorization code for tokens. Returns the token response JSON."""
    settings = get_settings()
    resp = httpx.post(
        TOKEN_URL,
        data={
            "code": code,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": settings.google_redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise ValueError(f"Token exchange failed: {resp.status_code}")
    return resp.json()


def fetch_userinfo(access_token: str) -> dict:
    resp = httpx.get(
        USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise ValueError(f"Userinfo fetch failed: {resp.status_code}")
    return resp.json()


def decode_state(state: str) -> uuid.UUID:
    payload = security.decode_token(state)
    return uuid.UUID(payload["sub"])


def upsert_connection(
    db: Session, user_id: uuid.UUID, tokens: dict, scopes: list[str]
) -> GoogleConnection:
    userinfo = fetch_userinfo(tokens["access_token"])
    encrypted = encryption.encrypt(tokens["refresh_token"])
    connection = db.scalar(select(GoogleConnection).where(GoogleConnection.user_id == user_id))
    if connection is None:
        connection = GoogleConnection(user_id=user_id)
        db.add(connection)
    connection.google_email = userinfo.get("email", "")
    connection.encrypted_refresh_token = encrypted
    connection.scopes = scopes or [SCOPE_DRIVE]
    connection.connected_at = datetime.now(UTC)
    connection.last_used_at = None
    connection.disconnected_at = None
    db.commit()
    db.refresh(connection)
    return connection


def get_credentials(db: Session, connection: GoogleConnection) -> Credentials:
    """Return refreshed credentials; access token lives in memory only."""
    settings = get_settings()
    creds = Credentials(
        token=None,
        refresh_token=encryption.decrypt(connection.encrypted_refresh_token),
        token_uri=TOKEN_URL,
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        scopes=connection.scopes or [SCOPE_DRIVE],
    )
    try:
        creds.refresh(Request())
    except RefreshError as exc:
        logger.warning("Google token refresh failed: %s", exc)
        raise GoogleReconnectRequired() from exc
    connection.last_used_at = datetime.now(UTC)
    db.commit()
    return creds


def disconnect(db: Session, connection: GoogleConnection) -> None:
    """Revoke the refresh token at Google and delete the stored row."""
    refresh_token = encryption.decrypt(connection.encrypted_refresh_token)
    try:
        httpx.post(REVOKE_URL, params={"token": refresh_token}, timeout=30)
    except httpx.HTTPError:
        logger.warning("Google revoke call failed; deleting local connection anyway")
    db.delete(connection)
    db.commit()
