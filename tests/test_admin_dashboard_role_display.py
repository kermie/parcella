"""
Issue #129: "still some 'role' apparencies in user administration" --
the admin dashboard's user list showed every user's raw UserRole
badge, including the inert READONLY default new users get (see ADR
0041: groups, not role, grant access to non-legacy accounts). Showing
"readonly" next to a user who is a full-access Administrators group
member is misleading, since role has zero effect on their permissions.

First pass only hid the badge for non-legacy users (falling back to a
blank cell). Reopened by the reporter: the column must show the user's
actual group assignments instead of a role/blank cell wherever
possible -- only a legacy ADMIN/BOARD account with no group membership
still falls back to the role badge.
"""
from app.database import AsyncSessionLocal
from app.models import User, UserRole, Group, GroupMembership
from app.auth import hash_password


async def web_login(client, email: str, password: str = "testpasswort123") -> None:
    response = await client.post("/auth/login", data={"email": email, "password": password})
    assert response.status_code in (302, 303)


async def _create_readonly_user(email: str, name: str) -> User:
    async with AsyncSessionLocal() as session:
        user = User(
            email=email,
            name=name,
            password_hash=hash_password("testpasswort123"),
            role=UserRole.READONLY,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def test_dashboard_hides_role_badge_for_non_legacy_user(client, admin_user):
    await web_login(client, "admin@example.com")
    await _create_readonly_user("member@example.com", "Group Managed User")

    page = await client.get("/admin/")
    assert page.status_code == 200
    assert "Group Managed User" in page.text
    assert "readonly" not in page.text.lower()


async def test_dashboard_shows_role_badge_for_legacy_admin_user(client, admin_user):
    await web_login(client, "admin@example.com")

    page = await client.get("/admin/")
    assert page.status_code == 200
    assert "Test-Admin" in page.text
    assert "Administrator" in page.text


async def test_dashboard_shows_group_names_instead_of_role_for_group_member(client, admin_user):
    """The actual ask from the reopened issue: a user assigned to a
    Group must show that group's name in the list, not a role badge or
    a blank cell."""
    await web_login(client, "admin@example.com")
    member = await _create_readonly_user("wasserwart@example.com", "Wasserwart User")

    async with AsyncSessionLocal() as session:
        group = Group(name="Wasserwarte")
        session.add(group)
        await session.flush()
        session.add(GroupMembership(user_id=member.id, group_id=group.id))
        await session.commit()

    page = await client.get("/admin/")
    assert page.status_code == 200
    assert "Wasserwart User" in page.text
    assert "Wasserwarte" in page.text
