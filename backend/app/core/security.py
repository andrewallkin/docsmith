import uuid
from datetime import UTC, datetime, timedelta

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import get_settings

ALGORITHM = "HS256"

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

REFRESH_COOKIE_NAME = "refresh_token"
REFRESH_COOKIE_PATH = "/api/auth"


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return pwd_context.verify(plain, hashed)
    except ValueError:
        return False


def create_token(subject: str, expires_delta: timedelta) -> tuple[str, uuid.UUID]:
    """Create a signed JWT; returns (token, jti)."""
    jti = uuid.uuid4()
    now = datetime.now(UTC)
    payload = {
        "sub": subject,
        "iat": now,
        "exp": now + expires_delta,
        "jti": str(jti),
    }
    token = jwt.encode(payload, get_settings().jwt_secret_key, algorithm=ALGORITHM)
    return token, jti


def create_access_token(subject: str) -> str:
    minutes = get_settings().jwt_access_token_expire_minutes
    token, _ = create_token(subject, timedelta(minutes=minutes))
    return token


def create_refresh_token(subject: str) -> tuple[str, uuid.UUID]:
    days = get_settings().jwt_refresh_token_expire_days
    return create_token(subject, timedelta(days=days))


def decode_token(token: str) -> dict:
    """Decode a JWT; raises ValueError on any validation failure."""
    try:
        payload = jwt.decode(
            token, get_settings().jwt_secret_key, algorithms=[ALGORITHM]
        )
    except JWTError as exc:
        raise ValueError("Invalid token") from exc
    if payload.get("sub") is None:
        raise ValueError("Invalid token")
    return payload


def set_refresh_cookie(response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path=REFRESH_COOKIE_PATH,
        max_age=settings.jwt_refresh_token_expire_days * 86400,
    )


def clear_refresh_cookie(response) -> None:
    response.delete_cookie(REFRESH_COOKIE_NAME, path=REFRESH_COOKIE_PATH)
