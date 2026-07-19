"""
Tests for the announcements module foundation (see
docs/module-announcements.md): authoring an Announcement (Markdown body,
image, optional print override) and its module-flag gating. Sending to
channels (blog/email/PDF) is not built yet, so not tested here.

Uses the web UI's cookie-based session login, since the announcements
router is a traditional web-form router (same reasoning as
tests/test_calendar.py), plus the JWT API to toggle the module flag
(same pattern as tests/test_public_api.py, since "announcements" also
defaults to False).
"""
from tests.conftest import login, auth_header


async def web_login(client, email: str, password: str = "testpasswort123") -> None:
    response = await client.post("/auth/login", data={"email": email, "password": password})
    assert response.status_code in (302, 303)


async def _enable_module(client, headers):
    response = await client.put(
        "/api/v1/club-settings/modul_announcements",
        json={"value": "true"},
        headers=headers,
    )
    assert response.status_code == 200, response.text


async def test_module_disabled_by_default(client, admin_user):
    # No flag flip here -- confirms the security-relevant default is
    # actually False, matching MODULE_DEFAULTS in app/module_flags.py.
    await web_login(client, "admin@example.com")
    response = await client.get("/announcements/")
    assert response.status_code == 404


async def test_create_and_edit_announcement(client, admin_user):
    token = await login(client, "admin@example.com")
    await _enable_module(client, auth_header(token))
    await web_login(client, "admin@example.com")

    create = await client.post(
        "/announcements/new",
        data={
            "title": "Autumn work session",
            "body_markdown": "Please join us **Saturday** for leaf clearing.",
        },
    )
    assert create.status_code == 303
    edit_url = create.headers["location"]
    assert edit_url.startswith("/announcements/") and edit_url.endswith("/edit")
    announcement_id = edit_url.split("/")[2]

    edit_page = await client.get(edit_url)
    assert edit_page.status_code == 200
    assert "Autumn work session" in edit_page.text

    # body_html must be derived from body_markdown (bold -> <strong>),
    # not just stored/echoed verbatim.
    from app.database import AsyncSessionLocal
    from app.models import Announcement
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Announcement).where(Announcement.id == announcement_id))
        announcement = result.scalar_one()
        assert "<strong>Saturday</strong>" in announcement.body_html
        assert announcement.status.value == "DRAFT"

    update = await client.post(
        edit_url,
        data={
            "title": "Autumn work session (updated)",
            "body_markdown": "Please join us **Saturday** for leaf clearing.",
            "print_text_override": "Join us Saturday for leaf clearing.",
        },
    )
    assert update.status_code == 303

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Announcement).where(Announcement.id == announcement_id))
        announcement = result.scalar_one()
        assert announcement.title == "Autumn work session (updated)"
        assert announcement.print_text_override == "Join us Saturday for leaf clearing."


async def test_missing_title_or_body_is_rejected(client, admin_user):
    token = await login(client, "admin@example.com")
    await _enable_module(client, auth_header(token))
    await web_login(client, "admin@example.com")

    response = await client.post("/announcements/new", data={"title": "", "body_markdown": ""})
    assert response.status_code == 400

    from app.database import AsyncSessionLocal
    from app.models import Announcement
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Announcement))
        assert result.scalars().all() == []


async def test_delete_announcement(client, admin_user):
    token = await login(client, "admin@example.com")
    await _enable_module(client, auth_header(token))
    await web_login(client, "admin@example.com")

    create = await client.post(
        "/announcements/new",
        data={"title": "To be deleted", "body_markdown": "Temporary content."},
    )
    announcement_id = create.headers["location"].split("/")[2]

    delete = await client.post(f"/announcements/{announcement_id}/delete")
    assert delete.status_code == 303

    from app.database import AsyncSessionLocal
    from app.models import Announcement
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Announcement).where(Announcement.id == announcement_id))
        assert result.scalar_one_or_none() is None


async def test_readonly_role_cannot_access_announcements(client, admin_user):
    """require_admin permits ADMIN and BOARD (board_user in conftest is
    already covered implicitly by the other tests, which all use
    admin_user); READONLY is the role that should actually be refused,
    same boundary as the Integrations page."""
    from app.database import AsyncSessionLocal
    from app.models import User, UserRole, ClubSetting
    from app.auth import hash_password

    async with AsyncSessionLocal() as session:
        session.add(ClubSetting(key="modul_announcements", value="true", description="test"))
        session.add(User(
            email="readonly@example.com", name="Test-Readonly",
            password_hash=hash_password("testpasswort123"), role=UserRole.READONLY,
        ))
        await session.commit()

    await web_login(client, "readonly@example.com")
    response = await client.get("/announcements/")
    assert response.status_code == 403
