import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_current_user_from_refresh_token
from app.config import get_settings
from app.core import security
from app.core.security import (
    clear_refresh_cookie,
    create_access_token,
    create_refresh_token,
    get_password_hash,
    set_refresh_cookie,
    verify_password,
)
from app.database import get_db
from app.models import RefreshSession, User
from app.schemas.auth import (
    AuthResponse,
    LoginRequest,
    PasswordChangeRequest,
    RegisterRequest,
    UserOut,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _ensure_authorized(email: str, context: str) -> None:
    allowed = get_settings().authorized_user_set
    if not allowed:
        return
    if email.lower() not in allowed:
        raise HTTPException(
            status_code=403,
            detail={
                "registration": "Registration is restricted to authorized users.",
                "login": "Access restricted. This account is not authorized to login.",
            }[context],
        )


def _issue_session(user: User, response: Response, db: Session) -> AuthResponse:
    access_token = create_access_token(str(user.id))
    refresh_token, refresh_jti = create_refresh_token(str(user.id))
    settings = get_settings()
    db.add(
        RefreshSession(
            id=refresh_jti,
            user_id=user.id,
            family_id=uuid.uuid4(),
            expires_at=datetime.now(UTC)
            + timedelta(days=settings.jwt_refresh_token_expire_days),
        )
    )
    db.commit()
    set_refresh_cookie(response, refresh_token)
    return AuthResponse(access_token=access_token, user=UserOut(id=str(user.id), email=user.email))


@router.post("/register", response_model=AuthResponse, status_code=201)
def register(payload: RegisterRequest, response: Response, db: Session = Depends(get_db)):
    email = payload.email.lower()
    _ensure_authorized(email, "registration")
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status_code=400, detail="Email already registered")
    user = User(email=email, password_hash=get_password_hash(payload.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return _issue_session(user, response, db)


@router.post("/login", response_model=AuthResponse)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    email = payload.email.lower()
    user = db.scalar(select(User).where(User.email == email))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    _ensure_authorized(email, "login")
    return _issue_session(user, response, db)


@router.post("/refresh", response_model=AuthResponse)
def refresh(request: Request, response: Response, db: Session = Depends(get_db)):
    user, session_row = get_current_user_from_refresh_token(
        request.cookies.get(security.REFRESH_COOKIE_NAME), db
    )
    # rotate: mark the used row, mint a new token in the same family
    session_row.is_used = True
    access_token = create_access_token(str(user.id))
    refresh_token, refresh_jti = create_refresh_token(str(user.id))
    settings = get_settings()
    db.add(
        RefreshSession(
            id=refresh_jti,
            user_id=user.id,
            family_id=session_row.family_id,
            expires_at=datetime.now(UTC)
            + timedelta(days=settings.jwt_refresh_token_expire_days),
        )
    )
    db.commit()
    set_refresh_cookie(response, refresh_token)
    return AuthResponse(access_token=access_token, user=UserOut(id=str(user.id), email=user.email))


@router.post("/logout", status_code=204)
def logout(
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # revokes every refresh session for the user (logout-everywhere semantics)
    rows = db.scalars(select(RefreshSession).where(RefreshSession.user_id == user.id)).all()
    for row in rows:
        row.is_revoked = True
    db.commit()
    clear_refresh_cookie(response)
    return None


@router.post("/password", status_code=204)
def change_password(
    payload: PasswordChangeRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    user.password_hash = get_password_hash(payload.new_password)
    rows = db.scalars(select(RefreshSession).where(RefreshSession.user_id == user.id)).all()
    for row in rows:
        row.is_revoked = True
    db.commit()
    return None
