"""Tests for login/authentication."""
from tests.conftest import login, auth_header


async def test_treasurer_role_no_longer_has_blanket_api_write_access(client):
    """ADR 0071 (amends ADR 0041): TREASURER used to be in
    require_write_access's role allow-list, granting blanket API write
    access to every module gated by it -- regardless of Group
    configuration, unlike the HTML side where TREASURER was already
    just the READONLY baseline widened by Groups. A TREASURER account
    with no relevant Group grant must now be blocked, same as HTML
    always was."""
    from app.database import AsyncSessionLocal
    from app.models import User, UserRole
    from app.auth import hash_password

    async with AsyncSessionLocal() as db:
        user = User(
            email="treasurer-plain@example.com", name="Plain Treasurer",
            password_hash=hash_password("testpasswort123"), role=UserRole.TREASURER,
        )
        db.add(user)
        await db.commit()

    token = await login(client, "treasurer-plain@example.com")
    headers = auth_header(token)

    response = await client.post(
        "/api/v1/members", json={"first_name": "Petra", "last_name": "Beispiel"}, headers=headers,
    )
    assert response.status_code == 403


async def test_login_success(client, admin_user):
    token = await login(client, "admin@example.com")
    assert token

    response = await client.get("/api/v1/auth/me", headers=auth_header(token))
    assert response.status_code == 200
    assert response.json()["email"] == "admin@example.com"


async def test_login_wrong_password(client, admin_user):
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "falsches-passwort"},
    )
    assert response.status_code == 401


async def test_login_unknown_email(client):
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "niemand@example.com", "password": "irgendwas"},
    )
    assert response.status_code == 401


async def test_protected_endpoint_without_token(client):
    response = await client.get("/api/v1/members")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Self-service password change (issue #149) -- any logged-in user, not
# just admins.
# ---------------------------------------------------------------------------

async def web_login(client, email: str, password: str = "testpasswort123") -> None:
    response = await client.post("/auth/login", data={"email": email, "password": password})
    assert response.status_code in (302, 303)


async def test_change_password_page_requires_login(client):
    resp = await client.get("/auth/change-password", follow_redirects=False)
    assert resp.status_code == 303


async def test_change_password_wrong_current_password_is_rejected(client, admin_user):
    await web_login(client, "admin@example.com")

    resp = await client.post(
        "/auth/change-password",
        data={
            "current_password": "wrong-password",
            "new_password": "newpassword123",
            "new_password_confirm": "newpassword123",
        },
    )
    assert resp.status_code == 400

    # Old password must still work -- nothing was changed.
    login_resp = await client.post(
        "/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"},
    )
    assert login_resp.status_code in (302, 303)


async def test_change_password_mismatched_confirmation_is_rejected(client, admin_user):
    await web_login(client, "admin@example.com")

    resp = await client.post(
        "/auth/change-password",
        data={
            "current_password": "testpasswort123",
            "new_password": "newpassword123",
            "new_password_confirm": "different456",
        },
    )
    assert resp.status_code == 400


async def test_change_password_too_short_is_rejected(client, admin_user):
    await web_login(client, "admin@example.com")

    resp = await client.post(
        "/auth/change-password",
        data={
            "current_password": "testpasswort123",
            "new_password": "short",
            "new_password_confirm": "short",
        },
    )
    assert resp.status_code == 400


async def test_change_password_success(client, admin_user):
    await web_login(client, "admin@example.com")

    resp = await client.post(
        "/auth/change-password",
        data={
            "current_password": "testpasswort123",
            "new_password": "newpassword123",
            "new_password_confirm": "newpassword123",
        },
    )
    assert resp.status_code == 200

    # Old password no longer works, new one does.
    old_login = await client.post(
        "/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"},
    )
    assert old_login.status_code == 401

    new_login = await client.post(
        "/auth/login", data={"email": "admin@example.com", "password": "newpassword123"},
    )
    assert new_login.status_code in (302, 303)
