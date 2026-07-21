"""Tests for login/authentication."""
from tests.conftest import login, auth_header


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
