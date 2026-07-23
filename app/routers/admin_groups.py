"""
Admin UI for managing groups and their per-module permissions (see
app/permissions.py and app/models.py's Group/GroupMembership/
GroupModulePermission). ADMIN/BOARD accounts don't need a group -- they
already bypass this system entirely -- so membership assignment here
only makes practical sense for TREASURER/READONLY users. This router is
ADMIN-only (require_system_admin), not BOARD -- see app/auth.py.
"""
import urllib.parse

from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Group, GroupMembership, GroupModulePermission, User, UserRole
from app.auth import require_system_admin
from app.permissions import MODULES
from app.i18n import t_for
from app.templating import templates

router = APIRouter(prefix="/admin/groups", tags=["admin"])


async def _load_groups(db: AsyncSession):
    result = await db.execute(
        select(Group)
        .options(
            selectinload(Group.permissions),
            selectinload(Group.memberships).selectinload(GroupMembership.user),
        )
        .order_by(Group.name)
    )
    return result.scalars().all()


@router.get("/", response_class=HTMLResponse)
async def groups_list(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_system_admin(request, db)

    groups = await _load_groups(db)

    # Users assignable to a group -- ADMIN/BOARD bypass the group system
    # entirely (see app/permissions.py), so they're deliberately left
    # out of the "add member" dropdowns.
    assignable_result = await db.execute(
        select(User)
        .where(User.role.in_([UserRole.TREASURER, UserRole.READONLY]))
        .order_by(User.name)
    )
    assignable_users = assignable_result.scalars().all()

    return templates.TemplateResponse(
        "admin/groups.html",
        {
            "request": request,
            "user": user,
            "groups": groups,
            "assignable_users": assignable_users,
            "MODULES": MODULES,
        },
    )


@router.post("/create")
async def group_create(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_system_admin(request, db)

    name = name.strip()
    description = description.strip() or None

    if not name:
        return RedirectResponse(
            f"/admin/groups/?fehler={urllib.parse.quote(t_for(request, 'errors.group_name_required'))}",
            status_code=302,
        )

    existing = await db.execute(select(Group).where(Group.name == name))
    if existing.scalar_one_or_none():
        return RedirectResponse(
            f"/admin/groups/?fehler={urllib.parse.quote(t_for(request, 'errors.group_name_taken'))}",
            status_code=302,
        )

    group = Group(name=name, description=description)
    db.add(group)
    await db.flush()
    for module in MODULES:
        db.add(GroupModulePermission(group_id=group.id, module=module))
    await db.commit()

    return RedirectResponse(
        f"/admin/groups/?erfolg={urllib.parse.quote(t_for(request, 'errors.group_created'))}",
        status_code=302,
    )


@router.post("/{group_id}/update")
async def group_update(
    group_id: str,
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_system_admin(request, db)
    form = await request.form()

    result = await db.execute(
        select(Group).options(selectinload(Group.permissions)).where(Group.id == group_id)
    )
    group = result.scalar_one_or_none()
    if not group:
        return RedirectResponse(
            f"/admin/groups/?fehler={urllib.parse.quote(t_for(request, 'errors.group_not_found'))}",
            status_code=302,
        )

    name = name.strip()
    if not name:
        return RedirectResponse(
            f"/admin/groups/?fehler={urllib.parse.quote(t_for(request, 'errors.group_name_required'))}",
            status_code=302,
        )
    duplicate = await db.execute(
        select(Group).where(Group.name == name, Group.id != group_id)
    )
    if duplicate.scalar_one_or_none():
        return RedirectResponse(
            f"/admin/groups/?fehler={urllib.parse.quote(t_for(request, 'errors.group_name_taken'))}",
            status_code=302,
        )

    group.name = name
    group.description = description.strip() or None

    permissions_by_module = {p.module: p for p in group.permissions}
    for module in MODULES:
        permission = permissions_by_module.get(module)
        if permission is None:
            permission = GroupModulePermission(group_id=group.id, module=module)
            db.add(permission)
        permission.can_read = form.get(f"read_{module}") == "on"
        permission.can_write = form.get(f"write_{module}") == "on"
        permission.can_delete = form.get(f"delete_{module}") == "on"

    await db.commit()

    return RedirectResponse(
        f"/admin/groups/?erfolg={urllib.parse.quote(t_for(request, 'errors.group_updated'))}",
        status_code=302,
    )


@router.post("/{group_id}/delete")
async def group_delete(
    group_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await require_system_admin(request, db)

    result = await db.execute(select(Group).where(Group.id == group_id))
    group = result.scalar_one_or_none()
    if group:
        await db.delete(group)
        await db.commit()

    return RedirectResponse(
        f"/admin/groups/?erfolg={urllib.parse.quote(t_for(request, 'errors.group_deleted'))}",
        status_code=302,
    )


@router.post("/{group_id}/members/add")
async def group_member_add(
    group_id: str,
    request: Request,
    user_id: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    await require_system_admin(request, db)

    group_result = await db.execute(select(Group).where(Group.id == group_id))
    if not group_result.scalar_one_or_none():
        return RedirectResponse(
            f"/admin/groups/?fehler={urllib.parse.quote(t_for(request, 'errors.group_not_found'))}",
            status_code=302,
        )

    existing = await db.execute(
        select(GroupMembership).where(
            GroupMembership.group_id == group_id, GroupMembership.user_id == user_id
        )
    )
    if existing.scalar_one_or_none():
        return RedirectResponse(
            f"/admin/groups/?fehler={urllib.parse.quote(t_for(request, 'errors.user_already_in_group'))}",
            status_code=302,
        )

    db.add(GroupMembership(group_id=group_id, user_id=user_id))
    await db.commit()

    return RedirectResponse(
        f"/admin/groups/?erfolg={urllib.parse.quote(t_for(request, 'errors.member_added'))}",
        status_code=302,
    )


@router.post("/{group_id}/members/{membership_id}/remove")
async def group_member_remove(
    group_id: str,
    membership_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await require_system_admin(request, db)

    result = await db.execute(
        select(GroupMembership).where(
            GroupMembership.id == membership_id, GroupMembership.group_id == group_id
        )
    )
    membership = result.scalar_one_or_none()
    if membership:
        await db.delete(membership)
        await db.commit()

    return RedirectResponse(
        f"/admin/groups/?erfolg={urllib.parse.quote(t_for(request, 'errors.member_removed'))}",
        status_code=302,
    )
