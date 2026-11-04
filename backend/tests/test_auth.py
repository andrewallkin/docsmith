"""Dashboard auth: registration allowlist, login, refresh rotation,
reuse detection, logout, password change (design doc §4.1, §13)."""



def test_register_and_login(client, db):
    res = client.post(
        "/api/auth/register", json={"email": "User@Example.com", "password": "password123"}
    )
    assert res.status_code == 201
    body = res.json()
    assert body["user"]["email"] == "user@example.com"  # lowercased
    assert body["access_token"]

    res = client.post(
        "/api/auth/login", json={"email": "user@example.com", "password": "password123"}
    )
    assert res.status_code == 200
    assert res.json()["access_token"]


def test_register_duplicate_email(client):
    client.post("/api/auth/register", json={"email": "x@example.com", "password": "password123"})
    res = client.post(
        "/api/auth/register", json={"email": "x@example.com", "password": "password123"}
    )
    assert res.status_code == 400


def test_login_wrong_password(client):
    client.post("/api/auth/register", json={"email": "x@example.com", "password": "password123"})
    res = client.post("/api/auth/login", json={"email": "x@example.com", "password": "wrongpass1"})
    assert res.status_code == 401


def test_registration_allowlist(monkeypatch, client):
    from app.config import get_settings

    monkeypatch.setattr(
        type(get_settings()),
        "authorized_user_set",
        property(lambda self: frozenset({"ok@example.com"})),
    )
    res = client.post(
        "/api/auth/register",
        json={"email": "nope@example.com", "password": "password123"},
    )
    assert res.status_code == 403
    res = client.post(
        "/api/auth/register", json={"email": "ok@example.com", "password": "password123"}
    )
    assert res.status_code == 201


def test_login_allowlist(monkeypatch, client):
    from app.config import get_settings

    monkeypatch.setattr(
        type(get_settings()),
        "authorized_user_set",
        property(lambda self: frozenset({"ok@example.com"})),
    )
    client.post("/api/auth/register", json={"email": "ok@example.com", "password": "password123"})
    res = client.post(
        "/api/auth/login", json={"email": "ok@example.com", "password": "password123"}
    )
    assert res.status_code == 200


def test_refresh_rotation_and_reuse_detection(client, user_and_headers, db):
    # log in, capture the refresh cookie
    res = client.post(
        "/api/auth/login", json={"email": "test@example.com", "password": "password123"}
    )
    old_cookie = client.cookies.get("refresh_token")

    # rotate once: new cookie issued, old marked used
    res = client.post("/api/auth/refresh", headers=user_and_headers)
    assert res.status_code == 200
    assert res.json()["access_token"]
    # the old cookie must no longer work
    client.cookies.clear()
    res = client.post("/api/auth/refresh", headers={"Cookie": f"refresh_token={old_cookie}"})
    assert res.status_code == 401

    # reuse detection revokes the entire token family: even the NEW cookie dies
    res = client.post(
        "/api/auth/login", json={"email": "test@example.com", "password": "password123"}
    )
    client.cookies.clear()
    res = client.post("/api/auth/refresh", headers={"Cookie": f"refresh_token={old_cookie}"})
    assert res.status_code == 401  # reuse -> family revoked
    res = client.post(
        "/api/auth/login", json={"email": "test@example.com", "password": "password123"}
    )
    new_cookie = client.cookies.get("refresh_token")
    client.post("/api/auth/refresh", headers=user_and_headers)  # rotate new
    client.cookies.clear()
    res = client.post("/api/auth/refresh", headers={"Cookie": f"refresh_token={new_cookie}"})
    assert res.status_code == 401


def test_logout_revokes_all_sessions(client, user_and_headers):
    client.post("/api/auth/login", json={"email": "test@example.com", "password": "password123"})
    res = client.post("/api/auth/logout", headers=user_and_headers)
    assert res.status_code == 204
    res = client.post("/api/auth/refresh", headers=user_and_headers)
    assert res.status_code == 401


def test_change_password(client, user_and_headers):
    res = client.post(
        "/api/auth/password",
        json={"current_password": "password123", "new_password": "newpassword1"},
        headers=user_and_headers,
    )
    assert res.status_code == 204
    res = client.post(
        "/api/auth/login", json={"email": "test@example.com", "password": "newpassword1"}
    )
    assert res.status_code == 200


def test_refresh_requires_cookie(client):
    res = client.post("/api/auth/refresh")
    assert res.status_code == 401
