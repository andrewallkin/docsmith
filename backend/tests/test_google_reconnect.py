"""Undecryptable stored tokens must surface as needing reconnect, not 500."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from app.models import GoogleConnection
from app.services import google_oauth_service


def _connection(db: Session, user) -> GoogleConnection:
    conn = GoogleConnection(
        user_id=user.id,
        google_email="x@example.com",
        encrypted_refresh_token="not-a-fernet-token",
        scopes=["https://www.googleapis.com/auth/drive"],
        connected_at=datetime.now(UTC),
    )
    db.add(conn)
    db.commit()
    db.refresh(conn)
    return conn


def test_get_credentials_undecryptable_token_raises_reconnect(db, user_and_headers, client):
    from app.models import User

    user = db.query(User).filter(User.email == "test@example.com").first()
    conn = _connection(db, user)
    with pytest.raises(google_oauth_service.GoogleReconnectRequired):
        google_oauth_service.get_credentials(db, conn)


def test_get_credentials_undecryptable_token_marks_disconnected(db, user_and_headers):
    from app.models import User

    user = db.query(User).filter(User.email == "test@example.com").first()
    conn = _connection(db, user)
    with pytest.raises(google_oauth_service.GoogleReconnectRequired):
        google_oauth_service.get_credentials(db, conn)
    db.refresh(conn)
    assert conn.disconnected_at is not None
