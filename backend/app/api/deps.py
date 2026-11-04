import uuid
from datetime import UTC, datetime

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import security
from app.core.tool_errors import google_not_connected
from app.database import get_db
from app.models import ApiKey, GoogleConnection, RefreshSession, User

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = security.decode_token(credentials.credentials)
    except ValueError:
        raise HTTPException(
            status_code=401,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    user = db.get(User, uuid.UUID(payload["sub"]))
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def _utc(value: datetime) -> datetime:
    """SQLite returns naive datetimes; treat those as UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def get_current_user_from_refresh_token(
    refresh_token: str | None, db: Session
) -> tuple[User, RefreshSession]:
    """Validate a refresh cookie token with rotation + reuse detection.

    Returns the user and the (still valid, unused) session row.
    Raises HTTPException(401) on any failure.
    """
    credentials_exception = HTTPException(
        status_code=401,
        detail="Invalid refresh token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not refresh_token:
        raise credentials_exception
    try:
        payload = security.decode_token(refresh_token)
    except ValueError:
        raise credentials_exception from None

    jti = uuid.UUID(payload["jti"])
    session_row = db.scalar(
        select(RefreshSession).where(RefreshSession.id == jti)
    )
    if session_row is None or session_row.is_revoked:
        raise credentials_exception

    now = datetime.now(UTC)
    if _utc(session_row.expires_at) <= now or session_row.is_used:
        # Reuse of an already-rotated token, or an expired session:
        # revoke the whole token family and reject.
        family_rows = db.scalars(
            select(RefreshSession).where(RefreshSession.family_id == session_row.family_id)
        ).all()
        for row in family_rows:
            row.is_revoked = True
        db.commit()
        raise credentials_exception

    user = db.get(User, session_row.user_id)
    if user is None:
        raise credentials_exception
    return user, session_row


class ToolAuthContext:
    """Everything a tool endpoint needs, derived from the API key alone."""

    def __init__(self, user: User, api_key: ApiKey, connection: GoogleConnection, db: Session):
        self.user = user
        self.api_key = api_key
        self.connection = connection
        self.db = db


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="Invalid or revoked API key",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_tool_auth_context(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> ToolAuthContext:
    """Bearer API key auth for /api/v1/tools/*.

    The key is the only mapping to the user and their Google credentials;
    caller-supplied identity is never accepted.
    """
    if credentials is None or not credentials.credentials.startswith("ds_live_"):
        raise _unauthorized()
    token = credentials.credentials

    from app.services.api_key_service import hash_key

    key_row = db.scalar(select(ApiKey).where(ApiKey.key_hash == hash_key(token)))
    if key_row is None or key_row.revoked_at is not None:
        raise _unauthorized()

    user = db.get(User, key_row.user_id)
    if user is None:
        raise _unauthorized()

    connection = db.scalar(
        select(GoogleConnection).where(
            GoogleConnection.user_id == user.id,
            GoogleConnection.disconnected_at.is_(None),
        )
    )
    if connection is None:
        raise google_not_connected()

    # Fire-and-forget bookkeeping (no audit table).
    now = datetime.now(UTC)
    key_row.last_used_at = now
    connection.last_used_at = now
    db.commit()

    return ToolAuthContext(user, key_row, connection, db)
