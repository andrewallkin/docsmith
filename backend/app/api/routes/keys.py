import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.models import ApiKey, User
from app.schemas.api_keys import ApiKeyCreate, ApiKeyCreated, ApiKeyOut
from app.services import api_key_service

router = APIRouter(prefix="/keys", tags=["keys"])


def _key_out(row: ApiKey) -> ApiKeyOut:
    return ApiKeyOut(
        id=str(row.id),
        name=row.name,
        key_prefix=row.key_prefix,
        created_at=row.created_at.isoformat() if row.created_at else "",
        last_used_at=row.last_used_at.isoformat() if row.last_used_at else None,
        revoked_at=row.revoked_at.isoformat() if row.revoked_at else None,
    )


@router.get("", response_model=list[ApiKeyOut])
def list_keys(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return [_key_out(row) for row in api_key_service.list_keys(db, user.id)]


@router.post("", response_model=ApiKeyCreated, status_code=201)
def create_key(
    payload: ApiKeyCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    row, full_key = api_key_service.create_key(db, user.id, payload.name)
    return ApiKeyCreated(
        key=_key_out(row),
        full_key=full_key,
    )


@router.delete("/{key_id}", status_code=204)
def revoke_key(
    key_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    try:
        parsed = uuid.UUID(key_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Key not found") from None
    row = api_key_service.revoke_key(db, user.id, parsed)
    if row is None:
        raise HTTPException(status_code=404, detail="Key not found")
    return None
