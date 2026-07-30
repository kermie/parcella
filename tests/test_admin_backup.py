"""
Issue #117: "add a backup possibility to administration panel".

Direct-download only (ADR 0053): a system admin triggers a real pg_dump
against the (test) database and gets the result back as a file
download -- nothing is ever written to server disk. This test runs a
REAL pg_dump subprocess against db_test, not a mock, per this repo's
"real Postgres, not fakes" testing philosophy (docs/testing.md).
"""


async def web_login(client, email: str, password: str = "testpasswort123") -> None:
    response = await client.post("/auth/login", data={"email": email, "password": password})
    assert response.status_code in (302, 303)


async def test_backup_download_returns_a_valid_pg_dump_file(client, admin_user):
    await web_login(client, "admin@example.com")

    resp = await client.post("/admin/backup/download")

    assert resp.status_code == 200
    content_disposition = resp.headers["content-disposition"]
    assert "attachment" in content_disposition
    assert ".dump" in content_disposition
    # Custom-format pg_dump magic header -- proves this is a real,
    # structurally valid dump, not just "some bytes came back".
    assert resp.content.startswith(b"PGDMP")


async def test_backup_download_requires_admin(client):
    """No session at all -- require_user (app/auth.py) redirects to login."""
    resp = await client.post("/admin/backup/download", follow_redirects=False)
    assert resp.status_code == 303


async def test_backup_download_shows_error_when_pg_dump_binary_missing(client, admin_user, monkeypatch):
    monkeypatch.setattr("app.routers.admin.PG_DUMP_BINARY", "/nonexistent/pg_dump")

    await web_login(client, "admin@example.com")
    resp = await client.post("/admin/backup/download", follow_redirects=False)

    assert resp.status_code == 302
    assert "error=" in resp.headers["location"]
