
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import get_settings
from app.database import get_db
from app.models import GoogleConnection, User
from app.schemas.google import GoogleConnectionStatus, GoogleConnectResponse
from app.services import google_oauth_service

router = APIRouter(prefix="/google", tags=["google"])


@router.get("/connect", response_model=GoogleConnectResponse)
def connect(user: User = Depends(get_current_user)):
    """Return the Google consent URL (the dashboard opens it)."""
    return GoogleConnectResponse(auth_url=google_oauth_service.build_auth_url(user.id))


@router.get("/callback")
def callback(code: str, state: str, db: Session = Depends(get_db)):
    """Exchange the OAuth code, store the encrypted refresh token, redirect.

    The failure reason is surfaced as &reason=<code> so a failed reauth is
    not silent (the dashboard can show it; no secrets in these messages).
    """
    try:
        user_id = google_oauth_service.decode_state(state)
    except Exception:
        return _error_redirect("invalid_state")
    try:
        tokens = google_oauth_service.exchange_code(code)
    except Exception:
        return _error_redirect("token_exchange_failed")
    try:
        scopes = (tokens.get("scope") or "").split(" ") if tokens.get("scope") else []
        google_oauth_service.upsert_connection(db, user_id, tokens, scopes)
    except Exception:
        return _error_redirect("connection_failed")
    url = f"{get_settings().frontend_url}/dashboard?google=success"
    return RedirectResponse(url=url)


def _error_redirect(reason: str) -> RedirectResponse:
    url = f"{get_settings().frontend_url}/dashboard?google=error&reason={reason}"
    return RedirectResponse(url=url)


@router.get("/status", response_model=GoogleConnectionStatus)
def status(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from sqlalchemy import select

    connection = db.scalar(
        select(GoogleConnection).where(
            GoogleConnection.user_id == user.id,
            GoogleConnection.disconnected_at.is_(None),
        )
    )
    if connection is None:
        return GoogleConnectionStatus(connected=False)
    expired = False
    try:
        google_oauth_service.get_credentials(db, connection)
    except google_oauth_service.GoogleReconnectRequired:
        expired = True
    return GoogleConnectionStatus(
        connected=True,
        google_email=connection.google_email,
        scopes=connection.scopes or [],
        connected_at=connection.connected_at.isoformat() if connection.connected_at else None,
        expired=expired,
    )


@router.delete("/connection", status_code=204)
def disconnect(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from sqlalchemy import select

    connection = db.scalar(
        select(GoogleConnection).where(
            GoogleConnection.user_id == user.id,
            GoogleConnection.disconnected_at.is_(None),
        )
    )
    if connection is None:
        raise HTTPException(status_code=404, detail="No Google connection")
    google_oauth_service.disconnect(db, connection)
    return None
