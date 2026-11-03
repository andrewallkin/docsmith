import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Configure settings BEFORE app modules are imported (get_settings is cached).
FERNET_KEY = "7_uWKh74qNCfi8Xkd2AAoHjPQcbUd-y829bdxKJosRE="
os.environ.update(
    {
        "JWT_SECRET_KEY": "test-secret",
        "ENCRYPTION_KEY": FERNET_KEY,
        "GOOGLE_CLIENT_ID": "test-client-id",
        "GOOGLE_CLIENT_SECRET": "test-client-secret",
        "GOOGLE_REDIRECT_URI": "http://localhost:8000/api/google/callback",
        "POSTGRES_HOST": "localhost",
        "AUTHORIZED_USERS": "",
    }
)

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def client(db):
    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    from fastapi.testclient import TestClient

    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def user_and_headers(client):
    res = client.post(
        "/api/auth/register", json={"email": "test@example.com", "password": "password123"}
    )
    assert res.status_code == 201
    token = res.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def active_key(client, user_and_headers, db):
    from app.core import encryption
    from app.models import GoogleConnection

    res = client.post("/api/keys", json={"name": "grok"}, headers=user_and_headers)
    full_key = res.json()["full_key"]
    # attach an active (non-expired) google connection so tool auth succeeds
    connection = GoogleConnection(
        google_email="test@gmail.com",
        encrypted_refresh_token=encryption.encrypt("fake-refresh-token"),
        scopes=["https://www.googleapis.com/auth/drive.file"],
    )
    from app.models import User

    user = db.query(User).first()
    connection.user_id = user.id
    db.add(connection)
    db.commit()
    return full_key
