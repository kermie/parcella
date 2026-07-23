"""
Group-based permission matrix. ADMIN and BOARD always bypass this
entirely and get full read/write/delete on every module (see
app/auth.py's require_admin) -- the administration panel itself is a
separate, narrower check (require_system_admin, ADMIN only). Everyone
else (TREASURER/READONLY) starts from a small baseline -- read-only on
members_parcels, nothing else -- and a group narrowly WIDENS that,
e.g. "handles work-hours sessions but shouldn't be able to edit the
member list." See ADR 0039.

Deliberately does NOT cover: tasks, announcements, cloud_storage,
public_signup_api, or the admin section itself. All of those are
already admin/board-only (see app/module_flags.py's off-by-default
modules and ADR 0034 for tasks) and stay that way regardless of group
configuration -- this system only ever widens what a TREASURER/READONLY
user can do beyond the baseline, it never touches anything currently
locked to admin/board. Also out of scope for this pass: the REST API
(app/api_auth.py) -- a separate JWT-based role system with its own
require_write_access, untouched here. See ADR 0038.
"""
from typing import Dict, Optional

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Group, GroupModulePermission, GroupMembership, User, UserRole

MODULES = [
    "members_parcels", "work_hours", "water", "electricity",
    "insurance", "tickets", "purchase_requests", "calendar", "inventory",
]

_EMPTY_PERMISSION = {"read": False, "write": False, "delete": False}
_FULL_PERMISSION = {"read": True, "write": True, "delete": True}


async def get_user_permissions(db: AsyncSession, user: Optional[User]) -> Dict[str, Dict[str, bool]]:
    """
    Effective read/write/delete per module for `user`. ADMIN/BOARD get
    full access to every module unconditionally. Anyone else: union
    across every group they belong to (most permissive group wins per
    module, per permission level).
    """
    if user is None:
        return {module: dict(_EMPTY_PERMISSION) for module in MODULES}
    if user.role in (UserRole.ADMIN, UserRole.BOARD):
        return {module: dict(_FULL_PERMISSION) for module in MODULES}

    permissions = {module: dict(_EMPTY_PERMISSION) for module in MODULES}
    if "members_parcels" in permissions:
        permissions["members_parcels"]["read"] = True  # baseline: everyone can look up who's who / which parcel

    result = await db.execute(
        select(GroupModulePermission)
        .join(GroupMembership, GroupMembership.group_id == GroupModulePermission.group_id)
        .where(GroupMembership.user_id == user.id)
    )
    for row in result.scalars().all():
        if row.module not in permissions:
            continue  # a module removed from MODULES since this row was created
        p = permissions[row.module]
        p["read"] = p["read"] or row.can_read
        p["write"] = p["write"] or row.can_write
        p["delete"] = p["delete"] or row.can_delete
    return permissions


def has_permission(permissions: Dict[str, Dict[str, bool]], module: str, level: str) -> bool:
    return permissions.get(module, {}).get(level, False)


async def require_permission(request: Request, db: AsyncSession, module: str, level: str) -> User:
    """
    Same calling convention as require_user/require_admin in app/auth.py
    -- call inline as the first line of a route body, e.g.
    `user = await require_permission(request, db, "members_parcels", "write")`.
    Reads the per-request cache the permissions_middleware already
    computed (see app/main.py) instead of re-querying when possible.
    """
    from app.auth import require_user  # local import: auth.py doesn't need to know about permissions.py

    user = await require_user(request, db)
    permissions = getattr(request.state, "permissions", None)
    if permissions is None:
        permissions = await get_user_permissions(db, user)
    if not has_permission(permissions, module, level):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Keine Berechtigung")
    return user


def jinja_has_perm(request: Request, module: str, level: str = "read") -> bool:
    """Jinja global (see app/templating.py): `{% if has_perm(request, 'work_hours') %}`."""
    permissions = getattr(request.state, "permissions", {})
    return has_permission(permissions, module, level)
