"""
Issue #150: "upload own avatar in admin/users/... this avatar should
appear at all of my changes, together with my user name, in every
place of Parcella where my user name appears."

Avatars are stored per-user under app/static/uploads/avatars/<user_id>.<ext>
(app/avatars.py) -- a real, git-ignored subdirectory, not routed through
the test DB, same situation as app/branding.py's logo upload (see
tests/test_admin_branding.py). Each test here uses fresh user ids it
creates itself and cleans up the specific files it wrote, so no backup/
restore dance is needed (unlike the logo, there's no pre-existing
default avatar file to clobber).
"""
from pathlib import Path

from sqlalchemy import select

from app.avatars import AVATAR_UPLOAD_DIR
from app.database import AsyncSessionLocal
from app.models import User, UserRole
from app.auth import hash_password

_TINY_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


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


def _cleanup(user_id: str) -> None:
    for p in AVATAR_UPLOAD_DIR.glob(f"{user_id}.*"):
        p.unlink()


async def _reload(user: User) -> User:
    """Re-fetches a User in a fresh session -- session.refresh() requires
    the instance to already be persistent WITHIN that exact session,
    which fixture-created/test-created objects here never are."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.id == user.id))
        return result.scalar_one()


# ---------------------------------------------------------------------------
# Self-service upload (/auth/avatar) -- any logged-in user.
# ---------------------------------------------------------------------------

async def test_self_service_avatar_upload_saves_file_and_shows_in_sidebar(client, admin_user):
    await web_login(client, "admin@example.com")
    try:
        resp = await client.post(
            "/auth/avatar",
            files={"avatar": ("me.png", _TINY_PNG_BYTES, "image/png")},
        )
        assert resp.status_code == 200

        admin_user = await _reload(admin_user)
        assert admin_user.avatar_filename == f"{admin_user.id}.png"
        assert (AVATAR_UPLOAD_DIR / f"{admin_user.id}.png").read_bytes() == _TINY_PNG_BYTES

        page = await client.get("/")
        assert f"/static/uploads/avatars/{admin_user.id}.png" in page.text
    finally:
        _cleanup(admin_user.id)


async def test_self_service_avatar_invalid_type_rejected(client, admin_user):
    await web_login(client, "admin@example.com")
    try:
        resp = await client.post(
            "/auth/avatar",
            files={"avatar": ("me.txt", b"not an image", "text/plain")},
        )
        assert resp.status_code == 400
        assert "Invalid file type" in resp.text

        admin_user = await _reload(admin_user)
        assert admin_user.avatar_filename is None
    finally:
        _cleanup(admin_user.id)


async def test_self_service_avatar_too_large_rejected(client, admin_user):
    await web_login(client, "admin@example.com")
    try:
        oversized = _TINY_PNG_BYTES + (b"\x00" * (2 * 1024 * 1024 + 1))
        resp = await client.post(
            "/auth/avatar",
            files={"avatar": ("me.png", oversized, "image/png")},
        )
        assert resp.status_code == 400
        assert "too large" in resp.text
    finally:
        _cleanup(admin_user.id)


async def test_self_service_avatar_removal(client, admin_user):
    await web_login(client, "admin@example.com")
    try:
        await client.post(
            "/auth/avatar",
            files={"avatar": ("me.png", _TINY_PNG_BYTES, "image/png")},
        )
        assert (AVATAR_UPLOAD_DIR / f"{admin_user.id}.png").exists()

        resp = await client.post("/auth/avatar/remove")
        assert resp.status_code in (302, 303)

        admin_user = await _reload(admin_user)
        assert admin_user.avatar_filename is None
        assert not (AVATAR_UPLOAD_DIR / f"{admin_user.id}.png").exists()
    finally:
        _cleanup(admin_user.id)


async def test_avatar_upload_requires_login(client):
    resp = await client.post(
        "/auth/avatar",
        files={"avatar": ("me.png", _TINY_PNG_BYTES, "image/png")},
        follow_redirects=False,
    )
    assert resp.status_code == 303


# ---------------------------------------------------------------------------
# Admin-side upload for another user (/admin/users/{id}/avatar).
# ---------------------------------------------------------------------------

async def test_admin_can_upload_avatar_for_another_user(client, admin_user):
    await web_login(client, "admin@example.com")
    member = await _create_readonly_user("avatartarget@example.com", "Avatar Target")
    try:
        resp = await client.post(
            f"/admin/users/{member.id}/avatar",
            files={"avatar": ("member.png", _TINY_PNG_BYTES, "image/png")},
        )
        assert resp.status_code in (302, 303)

        member = await _reload(member)
        assert member.avatar_filename == f"{member.id}.png"

        page = await client.get(f"/admin/users/{member.id}/edit")
        assert f"/static/uploads/avatars/{member.id}.png" in page.text
    finally:
        _cleanup(member.id)


async def test_admin_avatar_invalid_type_redirects_with_error(client, admin_user):
    await web_login(client, "admin@example.com")
    member = await _create_readonly_user("avatarbad@example.com", "Avatar Bad")
    try:
        resp = await client.post(
            f"/admin/users/{member.id}/avatar",
            files={"avatar": ("member.txt", b"not an image", "text/plain")},
        )
        assert resp.status_code in (302, 303)
        assert "avatar_error=invalid_avatar_type" in resp.headers.get("location", "")
    finally:
        _cleanup(member.id)


async def test_non_admin_cannot_upload_avatar_for_another_user(client, admin_user):
    member = await _create_readonly_user("avatarnonadmin@example.com", "Not Admin")
    await web_login(client, "avatarnonadmin@example.com")
    try:
        resp = await client.post(
            f"/admin/users/{admin_user.id}/avatar",
            files={"avatar": ("member.png", _TINY_PNG_BYTES, "image/png")},
            follow_redirects=False,
        )
        assert resp.status_code == 403

        admin_user = await _reload(admin_user)
        assert admin_user.avatar_filename is None
    finally:
        _cleanup(admin_user.id)


# ---------------------------------------------------------------------------
# Shown across the app wherever a system user's name appears (task board).
# ---------------------------------------------------------------------------

async def test_avatar_shown_on_task_board_assignee_chip(client, admin_user):
    from app.models import Task, TaskList, TaskAssignee, new_uuid

    await web_login(client, "admin@example.com")
    try:
        await client.post(
            "/auth/avatar",
            files={"avatar": ("me.png", _TINY_PNG_BYTES, "image/png")},
        )

        async with AsyncSessionLocal() as session:
            task_list = TaskList(id=new_uuid(), name="To do", position=0)
            session.add(task_list)
            await session.flush()
            task = Task(id=new_uuid(), title="Avatar test task", list_id=task_list.id, position=0)
            session.add(task)
            await session.flush()
            session.add(TaskAssignee(id=new_uuid(), task_id=task.id, user_id=admin_user.id))
            await session.commit()

        page = await client.get("/tasks/")
        assert page.status_code == 200
        assert f"/static/uploads/avatars/{admin_user.id}.png" in page.text
    finally:
        _cleanup(admin_user.id)
