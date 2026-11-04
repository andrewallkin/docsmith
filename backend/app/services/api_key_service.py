import hashlib
import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ApiKey

KEY_PREFIX = "ds_live_"
HASH_LOOKUP_CHARS = 12  # display prefix length ("ds_live_" + 4 hex chars)


def hash_key(full_key: str) -> str:
    return hashlib.sha256(full_key.encode()).hexdigest()


def generate_key() -> tuple[str, str, str]:
    """Generate a new API key. Returns (full_key, sha256_hash, display_prefix).

    The full key is returned exactly once (at creation); only the hash and a
    12-char display prefix are ever stored.
    """
    full_key = f"{KEY_PREFIX}{secrets.token_hex(16)}"
    return full_key, hash_key(full_key), full_key[:HASH_LOOKUP_CHARS]


def create_key(db: Session, user_id: uuid.UUID, name: str) -> tuple[ApiKey, str]:
    full_key, key_hash, key_prefix = generate_key()
    row = ApiKey(user_id=user_id, name=name, key_hash=key_hash, key_prefix=key_prefix)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row, full_key


def list_keys(db: Session, user_id: uuid.UUID) -> list[ApiKey]:
    return list(
        db.scalars(
            select(ApiKey)
            .where(ApiKey.user_id == user_id)
            .order_by(ApiKey.created_at.desc())
        )
    )


def revoke_key(db: Session, user_id: uuid.UUID, key_id: uuid.UUID) -> ApiKey | None:
    row = db.scalar(select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == user_id))
    if row is None:
        return None
    row.revoked_at = row.revoked_at or func_now()
    db.commit()
    db.refresh(row)
    return row


def func_now():
    from datetime import UTC, datetime

    return datetime.now(UTC)
