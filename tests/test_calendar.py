"""
Tests for the calendar module: community calendar entries (merged with
work sessions), the public ICS feed, token protection on the private
feeds, and the council-absence self-service permission rule (anyone can
log their own absence, nobody can delete someone else's).

Uses the web UI's cookie-based session login (not the JWT API), since
the calendar module is web-UI-only -- httpx's AsyncClient keeps cookies
across requests within a test automatically.
"""
from datetime import date, timedelta

from tests.conftest import login, auth_header


async def web_login(client, email: str, password: str = "testpasswort123") -> None:
    """Logs in via the web UI's cookie-based session (not the JWT API) --
    the calendar module's routes are traditional web forms, so this is
    the login flow that actually applies to them."""
    response = await client.post("/auth/login", data={"email": email, "password": password})
    assert response.status_code in (302, 303)


async def test_community_calendar_and_public_ics(client, admin_user):
    await web_login(client, "admin@example.com")

    create = await client.post(
        "/calendar/community/new",
        data={
            "title": "Annual General Meeting",
            "event_type": "MEMBER_MEETING",
            "start_date": (date.today() + timedelta(days=30)).isoformat(),
        },
    )
    assert create.status_code in (302, 303)

    overview = await client.get("/calendar/community")
    assert overview.status_code == 200
    assert "Annual General Meeting" in overview.text

    # The ICS feed must be reachable with NO authentication at all --
    # it's meant to be embedded on the club's public website, which
    # can't send this app's session cookie.
    from httpx import AsyncClient, ASGITransport
    from app.main import app as fastapi_app

    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as anon_client:
        ics_response = await anon_client.get("/calendar/community.ics")
        assert ics_response.status_code == 200
        assert "Annual General Meeting" in ics_response.text
        assert "BEGIN:VCALENDAR" in ics_response.text


async def test_community_calendar_excludes_special_sessions(client, admin_user):
    """Only STANDARD work sessions belong on the community calendar --
    SPECIAL (spontaneous/unplanned) ones shouldn't appear in the list
    view or the public ICS feed."""
    await web_login(client, "admin@example.com")

    from app.database import AsyncSessionLocal
    from app.models import WorkSession, SessionType

    async with AsyncSessionLocal() as session:
        session.add(WorkSession(
            title="Planned Leaf Raking", type=SessionType.STANDARD,
            date=date.today() + timedelta(days=10),
        ))
        session.add(WorkSession(
            title="Spontaneous Bench Painting", type=SessionType.SPECIAL,
            date=date.today() + timedelta(days=5),
        ))
        await session.commit()

    overview = await client.get("/calendar/community")
    assert overview.status_code == 200
    assert "Planned Leaf Raking" in overview.text
    assert "Spontaneous Bench Painting" not in overview.text

    from httpx import AsyncClient, ASGITransport
    from app.main import app as fastapi_app

    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as anon_client:
        ics_response = await anon_client.get("/calendar/community.ics")
        assert ics_response.status_code == 200
        assert "Planned Leaf Raking" in ics_response.text
        assert "Spontaneous Bench Painting" not in ics_response.text


async def test_private_ics_feeds_require_correct_token(client, admin_user):
    await web_login(client, "admin@example.com")

    # No token, and a wrong token, must both be rejected.
    no_token = await client.get("/calendar/birthdays.ics")
    assert no_token.status_code == 403

    wrong_token = await client.get("/calendar/birthdays.ics?token=not-the-real-token")
    assert wrong_token.status_code == 403

    hub = await client.get("/calendar/")
    assert hub.status_code == 200
    import re
    match = re.search(r"birthdays\.ics\?token=([\w-]+)", hub.text)
    assert match, "Expected the birthday ICS URL with a token on the calendar hub page"
    real_token = match.group(1)

    correct = await client.get(f"/calendar/birthdays.ics?token={real_token}")
    assert correct.status_code == 200
    assert "BEGIN:VCALENDAR" in correct.text


async def test_council_absence_self_service_permissions(client, admin_user):
    await web_login(client, "admin@example.com")

    create = await client.post(
        "/calendar/council-absence/new",
        data={
            "start_date": (date.today() + timedelta(days=10)).isoformat(),
            "end_date": (date.today() + timedelta(days=15)).isoformat(),
            "note": "Vacation",
        },
    )
    assert create.status_code in (302, 303)

    overview = await client.get("/calendar/council-absence")
    assert "Vacation" in overview.text

    import re
    match = re.search(r"council-absence/([a-f0-9-]{36})/delete", overview.text)
    assert match
    entry_id = match.group(1)

    # A regular (non-admin/board) user must NOT be able to delete
    # someone else's entry -- admin/board CAN, for cleanup purposes,
    # which is why this needs a genuinely restricted role, not just a
    # different account.
    from app.database import AsyncSessionLocal
    from app.models import User, UserRole
    from app.auth import hash_password

    async with AsyncSessionLocal() as session:
        other_user = User(
            email="member@example.com",
            name="Test Member",
            password_hash=hash_password("testpasswort123"),
            role=UserRole.READONLY,
        )
        session.add(other_user)
        await session.commit()

    from httpx import AsyncClient, ASGITransport
    from app.main import app as fastapi_app

    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as other_client:
        await web_login(other_client, "member@example.com")
        forbidden = await other_client.post(f"/calendar/council-absence/{entry_id}/delete")
        assert forbidden.status_code == 403

    # The original user deleting their own entry must succeed.
    own_delete = await client.post(f"/calendar/council-absence/{entry_id}/delete")
    assert own_delete.status_code in (302, 303)
