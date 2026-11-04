"""API key lifecycle + tool auth tests (design doc §13)."""


from app.services.api_key_service import generate_key


def test_key_generation_format():
    full_key, key_hash, prefix = generate_key()
    assert full_key.startswith("ds_live_")
    assert len(full_key) == 40  # "ds_live_" + 32 hex chars
    assert prefix == full_key[:12]
    assert len(key_hash) == 64  # sha256 hex


def test_key_hash_is_sha256_never_plaintext():
    full_key, key_hash, _ = generate_key()
    import hashlib

    assert key_hash == hashlib.sha256(full_key.encode()).hexdigest()
    assert full_key not in key_hash


def test_create_key_stores_hash_and_prefix_only(client, user_and_headers, db):
    from app.models import ApiKey

    res = client.post("/api/keys", json={"name": "grok bot"}, headers=user_and_headers)
    assert res.status_code == 201
    body = res.json()
    assert body["full_key"].startswith("ds_live_")
    assert body["key"]["key_prefix"] == body["full_key"][:12]
    # plaintext is never persisted
    rows = db.query(ApiKey).all()
    assert len(rows) == 1
    assert body["full_key"] not in rows[0].key_hash
    assert rows[0].key_prefix == body["full_key"][:12]


def test_list_keys_hides_plaintext(client, user_and_headers):
    client.post("/api/keys", json={"name": "k1"}, headers=user_and_headers)
    res = client.get("/api/keys", headers=user_and_headers)
    assert res.status_code == 200
    keys = res.json()
    assert len(keys) == 1
    assert "full_key" not in keys[0]
    assert keys[0]["key_prefix"].startswith("ds_live_")


def test_tool_auth_invalid_key_401(client, db):
    res = client.post(
        "/api/v1/tools/documents/search",
        json={"query": "x"},
        headers={"Authorization": "Bearer ds_live_" + "0" * 32},
    )
    assert res.status_code == 401
    assert res.json()["detail"]


def test_tool_auth_revoked_key_401(client, user_and_headers):
    created = client.post("/api/keys", json={"name": "grok"}, headers=user_and_headers).json()
    full_key = created["full_key"]
    key_id = created["key"]["id"]
    res = client.delete(f"/api/keys/{key_id}", headers=user_and_headers)
    assert res.status_code == 204
    res = client.post(
        "/api/v1/tools/documents/search",
        json={"query": "x"},
        headers={"Authorization": f"Bearer {full_key}"},
    )
    assert res.status_code == 401


def test_tool_auth_malformed_bearer_401(client, user_and_headers):
    created = client.post("/api/keys", json={"name": "grok"}, headers=user_and_headers).json()
    key_id = created["key"]["id"]
    client.delete(f"/api/keys/{key_id}", headers=user_and_headers)
    res = client.post(
        "/api/v1/tools/documents/search",
        json={"query": "x"},
        headers={"Authorization": "Bearer not-a-key"},
    )
    assert res.status_code == 401


def test_tool_google_not_connected_409(client, user_and_headers):
    created = client.post("/api/keys", json={"name": "grok"}, headers=user_and_headers).json()
    res = client.post(
        "/api/v1/tools/documents/search",
        json={"query": "x"},
        headers={"Authorization": f"Bearer {created['full_key']}"},
    )
    assert res.status_code == 409
    assert res.json()["error"] == "google_not_connected"


def test_key_last_used_at_updates(client, active_key, db, monkeypatch):
    from app.models import ApiKey

    class FakeProvider:
        def __init__(self, credentials):
            pass

        def search_documents(self, query, limit=10, full_text=None, folder_id=None):
            return []

    from app.services import document_service

    monkeypatch.setattr(
        document_service, "_provider", lambda db, ctx: FakeProvider(None)
    )
    res = client.post(
        "/api/v1/tools/documents/search",
        json={"query": "x"},
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 200
    row = db.query(ApiKey).first()
    assert row.last_used_at is not None


def test_user_isolation_between_keys(client, db, monkeypatch):
    """User A's key can never reach user B's Google connection."""
    from app.core import encryption
    from app.models import GoogleConnection, User
    from app.services import document_service

    # two users, each with their own Google connection
    client.post("/api/auth/register", json={"email": "a@example.com", "password": "password123"})
    client.post("/api/auth/register", json={"email": "b@example.com", "password": "password123"})

    def login_headers(email):
        res = client.post("/api/auth/login", json={"email": email, "password": "password123"})
        return {"Authorization": f"Bearer {res.json()['access_token']}"}

    headers_a = login_headers("a@example.com")
    login_headers("b@example.com")

    for email in ("a", "b"):
        user = db.query(User).filter(User.email == f"{email}@example.com").first()
        db.add(
            GoogleConnection(
                user_id=user.id,
                google_email=f"{email}@gmail.com",
                encrypted_refresh_token=encryption.encrypt(f"{email}-refresh"),
                scopes=["https://www.googleapis.com/auth/drive"],
            )
        )
    db.commit()

    key_a = client.post("/api/keys", json={"name": "A"}, headers=headers_a).json()["full_key"]

    seen: dict = {}

    class FakeProvider:
        def __init__(self, credentials):
            seen["provider_built"] = True

        def search_documents(self, query, limit=10, full_text=None, folder_id=None):
            return []

    def fake_provider_factory(db_session, ctx):
        # mirrors the real _provider: resolve credentials from the connection
        seen["google_email"] = ctx.connection.google_email
        seen["connection_user_id"] = ctx.connection.user_id
        return FakeProvider(None)

    monkeypatch.setattr(
        document_service, "_provider", fake_provider_factory
    )

    res = client.post(
        "/api/v1/tools/documents/search",
        json={"query": "x"},
        headers={"Authorization": f"Bearer {key_a}"},
    )
    assert res.status_code == 200
    # credentials were resolved from user A's connection, never user B's
    user_b = db.query(User).filter(User.email == "b@example.com").first()
    assert seen["google_email"] == "a@gmail.com"
    assert seen["connection_user_id"] != user_b.id


def test_revoke_key_of_other_user_404(client, db):
    client.post("/api/auth/register", json={"email": "a@example.com", "password": "password123"})
    client.post("/api/auth/register", json={"email": "b@example.com", "password": "password123"})

    def login_headers(email):
        res = client.post("/api/auth/login", json={"email": email, "password": "password123"})
        return {"Authorization": f"Bearer {res.json()['access_token']}"}

    headers_a = login_headers("a@example.com")
    headers_b = login_headers("b@example.com")
    created = client.post("/api/keys", json={"name": "A"}, headers=headers_a).json()
    res = client.delete(f"/api/keys/{created['key']['id']}", headers=headers_b)
    assert res.status_code == 404
    # key A still valid for user A
    assert client.get("/api/keys", headers=headers_a).status_code == 200
