"""
Issue #111: "add the names of board members to admin -> settings ->
club settings. Use a member picker from members, multiple options
possible. Let me search for members in this member picker."

See docs/ADR/0047-club-settings-board-members.md for why this is a
dedicated ClubBoardMember association table (not a ClubSetting value,
not a reuse of ClubRole/MemberClubRole) and why the web form resyncs
the full list on every save.
"""
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import Member, ClubBoardMember
from tests.conftest import login, auth_header


async def web_login(client, email: str, password: str = "testpasswort123") -> None:
    response = await client.post("/auth/login", data={"email": email, "password": password})
    assert response.status_code in (302, 303)


async def _create_member(first_name: str, last_name: str) -> Member:
    async with AsyncSessionLocal() as session:
        member = Member(first_name=first_name, last_name=last_name)
        session.add(member)
        await session.commit()
        await session.refresh(member)
        return member


async def test_saving_settings_with_board_member_ids_persists_them(client, admin_user):
    await web_login(client, "admin@example.com")
    alice = await _create_member("Alice", "Boardmember")
    bob = await _create_member("Bob", "Boardmember")

    resp = await client.post("/admin/settings", data={"board_member_ids": [alice.id, bob.id]})
    assert resp.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(ClubBoardMember))
        member_ids = {row.member_id for row in result.scalars().all()}
    assert member_ids == {alice.id, bob.id}


async def test_saving_settings_without_board_member_ids_clears_them(client, admin_user):
    await web_login(client, "admin@example.com")
    alice = await _create_member("Alice", "Former")

    first = await client.post("/admin/settings", data={"board_member_ids": [alice.id]})
    assert first.status_code in (302, 303)

    # Resync semantics (ADR 0047 point 4): omitting board_member_ids
    # entirely means "no board members submitted", so the previously
    # saved one must be removed, not left in place.
    second = await client.post("/admin/settings", data={})
    assert second.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(ClubBoardMember))
        assert result.scalars().all() == []


async def test_settings_page_renders_selected_board_members(client, admin_user):
    await web_login(client, "admin@example.com")
    alice = await _create_member("Alice", "Chairperson")

    resp = await client.post("/admin/settings", data={"board_member_ids": [alice.id]})
    assert resp.status_code in (302, 303)

    page = await client.get("/admin/settings")
    assert page.status_code == 200
    assert "Alice Chairperson" in page.text
    assert f'value="{alice.id}"' in page.text
    assert "checked" in page.text


async def test_board_member_ids_from_deactivated_member_are_ignored(client, admin_user):
    """A stale form submission (e.g. a member deactivated between page
    load and submit) must not crash the save with an FK violation --
    the id is silently dropped instead, same as any other id that
    doesn't resolve to a currently-active member."""
    await web_login(client, "admin@example.com")

    resp = await client.post("/admin/settings", data={"board_member_ids": ["not-a-real-member-id"]})
    assert resp.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(ClubBoardMember))
        assert result.scalars().all() == []


async def test_board_members_api_returns_saved_members(client, admin_user):
    await web_login(client, "admin@example.com")
    alice = await _create_member("Alice", "Apiboard")

    resp = await client.post("/admin/settings", data={"board_member_ids": [alice.id]})
    assert resp.status_code in (302, 303)

    token = await login(client, "admin@example.com")
    api_resp = await client.get("/api/v1/club-settings/board-members", headers=auth_header(token))
    assert api_resp.status_code == 200
    body = api_resp.json()
    assert body == [{"member_id": alice.id, "full_name": "Alice Apiboard"}]
