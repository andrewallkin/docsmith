
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
    """Exchange the OAuth code, store the encrypted refresh token, redirect."""
    try:
        user_id = google_oauth_service.decode_state(state)
        tokens = google_oauth_service.exchange_code(code)
        scopes = (tokens.get("scope") or "").split(" ") if tokens.get("scope") else []
        google_oauth_service.upsert_connection(db, user_id, tokens, scopes)
        result = "success"
    except Exception:
        result = "error"
    return RedirectResponse(url=f"{get_settings().frontend_url}/dashboard?google={result}")


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
