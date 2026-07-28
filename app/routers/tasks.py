"""
Task board router (web UI): a general-purpose kanban board for club
business that isn't tied to a work session (see app/models.py for the
distinction from WorkTask). Admin/board only for both viewing and
editing, per explicit product decision. Columns are user-configurable
`TaskList` rows (issue #100, see ADR 0043) -- both cards and lists are
managed here.
"""
from datetime import date
from urllib.parse import quote as urlquote

from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Task, TaskList, User
from app.auth import require_admin
from app.i18n import t_for
from app.module_flags import require_module
from app.task_board import (
    next_position, move_task, close_gap_after_delete,
    next_list_position, move_list, delete_list,
)

router = APIRouter(
    prefix="/tasks",
    tags=["tasks"],
    dependencies=[Depends(require_module("tasks"))],
)
from app.templating import templates

# Maps task_board.delete_list()'s short ValueError codes to translation keys.
_LIST_DELETE_ERROR_KEYS = {
    "last_list": "tasks.errors.delete_list_last_list",
    "missing_target": "tasks.errors.delete_list_missing_target",
    "target_not_found": "tasks.errors.delete_list_target_not_found",
}


async def _get_task_or_404(db: AsyncSession, task_id: str, request: Request) -> Task:
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail=t_for(request, "tasks.errors.task_not_found"))
    return task


async def _get_list_or_404(db: AsyncSession, list_id: str, request: Request) -> TaskList:
    result = await db.execute(select(TaskList).where(TaskList.id == list_id))
    task_list = result.scalar_one_or_none()
    if not task_list:
        raise HTTPException(status_code=404, detail=t_for(request, "tasks.errors.list_not_found"))
    return task_list


async def _all_lists(db: AsyncSession):
    result = await db.execute(select(TaskList).order_by(TaskList.position))
    return result.scalars().all()


async def _active_users(db: AsyncSession):
    result = await db.execute(select(User).where(User.is_active == True).order_by(User.name))
    return result.scalars().all()


@router.get("/", response_class=HTMLResponse)
async def board(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_admin(request, db)

    result = await db.execute(
        select(TaskList)
        .options(selectinload(TaskList.tasks).selectinload(Task.assigned_to))
        .order_by(TaskList.position)
    )
    lists = result.scalars().all()

    return templates.TemplateResponse("tasks/board.html", {
        "request": request, "user": user,
        "lists": lists,
        "today": date.today(),
        "list_error": request.query_params.get("list_error"),
    })


@router.get("/new", response_class=HTMLResponse)
async def task_new_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_admin(request, db)
    return templates.TemplateResponse("tasks/form.html", {
        "request": request, "user": user, "task": None,
        "active_users": await _active_users(db),
        "lists": await _all_lists(db),
    })


@router.post("/new")
async def task_create(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    due_date: str = Form(""),
    assigned_to_id: str = Form(""),
    list_id: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_admin(request, db)

    lists = await _all_lists(db)
    target_list_id = list_id.strip() or lists[0].id

    task = Task(
        title=title.strip(),
        description=description.strip() or None,
        due_date=date.fromisoformat(due_date) if due_date.strip() else None,
        assigned_to_id=assigned_to_id.strip() or None,
        list_id=target_list_id,
        position=await next_position(db, target_list_id),
    )
    db.add(task)
    await db.commit()
    return RedirectResponse("/tasks/", status_code=302)


@router.post("/lists/new")
async def list_create(request: Request, name: str = Form(...), db: AsyncSession = Depends(get_db)):
    await require_admin(request, db)

    task_list = TaskList(name=name.strip(), position=await next_list_position(db))
    db.add(task_list)
    await db.commit()
    return RedirectResponse("/tasks/", status_code=302)


@router.post("/lists/{list_id}/move")
async def list_move(list_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    await require_admin(request, db)
    task_list = await _get_list_or_404(db, list_id, request)

    body = await request.json()
    try:
        new_position = int(body["position"])
    except (KeyError, ValueError):
        raise HTTPException(status_code=400, detail=t_for(request, "tasks.errors.invalid_move"))

    await move_list(db, task_list, new_position)
    return JSONResponse({"ok": True})


@router.post("/lists/{list_id}/edit")
async def list_rename(list_id: str, request: Request, name: str = Form(...), db: AsyncSession = Depends(get_db)):
    await require_admin(request, db)
    task_list = await _get_list_or_404(db, list_id, request)

    task_list.name = name.strip()
    await db.commit()
    return RedirectResponse("/tasks/", status_code=302)


@router.post("/lists/{list_id}/delete")
async def list_delete(
    list_id: str, request: Request,
    move_to_list_id: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_admin(request, db)
    task_list = await _get_list_or_404(db, list_id, request)

    try:
        await delete_list(db, task_list, move_to_list_id.strip() or None)
    except ValueError as e:
        message = urlquote(t_for(request, _LIST_DELETE_ERROR_KEYS[str(e)]))
        return RedirectResponse(f"/tasks/?list_error={message}", status_code=303)

    return RedirectResponse("/tasks/", status_code=302)


@router.get("/{task_id}/edit", response_class=HTMLResponse)
async def task_edit_page(task_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_admin(request, db)
    task = await _get_task_or_404(db, task_id, request)
    return templates.TemplateResponse("tasks/form.html", {
        "request": request, "user": user, "task": task,
        "active_users": await _active_users(db),
        "lists": await _all_lists(db),
    })


@router.post("/{task_id}/edit")
async def task_update(
    task_id: str,
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    due_date: str = Form(""),
    assigned_to_id: str = Form(""),
    list_id: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_admin(request, db)
    task = await _get_task_or_404(db, task_id, request)

    task.title = title.strip()
    task.description = description.strip() or None
    task.due_date = date.fromisoformat(due_date) if due_date.strip() else None
    task.assigned_to_id = assigned_to_id.strip() or None
    if list_id.strip() and list_id.strip() != task.list_id:
        await move_task(db, task, list_id.strip(), await next_position(db, list_id.strip()))

    await db.commit()
    return RedirectResponse("/tasks/", status_code=302)


@router.post("/{task_id}/move")
async def task_move(task_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    await require_admin(request, db)
    task = await _get_task_or_404(db, task_id, request)

    body = await request.json()
    try:
        new_list_id = str(body["list_id"])
        new_position = int(body["position"])
    except (KeyError, ValueError):
        raise HTTPException(status_code=400, detail=t_for(request, "tasks.errors.invalid_move"))

    await move_task(db, task, new_list_id, new_position)
    return JSONResponse({"ok": True})


@router.post("/{task_id}/delete")
async def task_delete(task_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    await require_admin(request, db)
    task = await _get_task_or_404(db, task_id, request)

    list_id, position = task.list_id, task.position
    await db.delete(task)
    await db.commit()
    await close_gap_after_delete(db, list_id, position)

    return RedirectResponse("/tasks/", status_code=302)
