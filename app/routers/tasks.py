"""
Task board router (web UI): a general-purpose kanban board for club
business that isn't tied to a work session (see app/models.py for the
distinction from WorkTask). Admin/board only for both viewing and
editing, per explicit product decision.
"""
from datetime import date

from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import Task, TaskStatus, User
from app.auth import require_admin
from app.i18n import t_for
from app.module_flags import require_module
from app.task_board import next_position, move_task, close_gap_after_delete

router = APIRouter(
    prefix="/tasks",
    tags=["tasks"],
    dependencies=[Depends(require_module("tasks"))],
)
from app.templating import templates


async def _get_task_or_404(db: AsyncSession, task_id: str, request: Request) -> Task:
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail=t_for(request, "tasks.errors.task_not_found"))
    return task


async def _active_users(db: AsyncSession):
    result = await db.execute(select(User).where(User.is_active == True).order_by(User.name))
    return result.scalars().all()


@router.get("/", response_class=HTMLResponse)
async def board(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_admin(request, db)

    result = await db.execute(select(Task).order_by(Task.status, Task.position))
    all_tasks = result.scalars().all()

    columns = {status: [] for status in TaskStatus}
    for task in all_tasks:
        columns[task.status].append(task)

    return templates.TemplateResponse("tasks/board.html", {
        "request": request, "user": user,
        "todo_tasks": columns[TaskStatus.TODO],
        "in_progress_tasks": columns[TaskStatus.IN_PROGRESS],
        "done_tasks": columns[TaskStatus.DONE],
        "today": date.today(),
        "TaskStatus": TaskStatus,
    })


@router.get("/new", response_class=HTMLResponse)
async def task_new_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_admin(request, db)
    return templates.TemplateResponse("tasks/form.html", {
        "request": request, "user": user, "task": None,
        "active_users": await _active_users(db),
    })


@router.post("/new")
async def task_create(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    due_date: str = Form(""),
    assigned_to_id: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_admin(request, db)

    task = Task(
        title=title.strip(),
        description=description.strip() or None,
        due_date=date.fromisoformat(due_date) if due_date.strip() else None,
        assigned_to_id=assigned_to_id.strip() or None,
        status=TaskStatus.TODO,
        position=await next_position(db, TaskStatus.TODO),
    )
    db.add(task)
    await db.commit()
    return RedirectResponse("/tasks/", status_code=302)


@router.get("/{task_id}/edit", response_class=HTMLResponse)
async def task_edit_page(task_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_admin(request, db)
    task = await _get_task_or_404(db, task_id, request)
    return templates.TemplateResponse("tasks/form.html", {
        "request": request, "user": user, "task": task,
        "active_users": await _active_users(db),
    })


@router.post("/{task_id}/edit")
async def task_update(
    task_id: str,
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    due_date: str = Form(""),
    assigned_to_id: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_admin(request, db)
    task = await _get_task_or_404(db, task_id, request)

    task.title = title.strip()
    task.description = description.strip() or None
    task.due_date = date.fromisoformat(due_date) if due_date.strip() else None
    task.assigned_to_id = assigned_to_id.strip() or None

    await db.commit()
    return RedirectResponse("/tasks/", status_code=302)


@router.post("/{task_id}/move")
async def task_move(task_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    await require_admin(request, db)
    task = await _get_task_or_404(db, task_id, request)

    body = await request.json()
    try:
        new_status = TaskStatus(body["status"])
        new_position = int(body["position"])
    except (KeyError, ValueError):
        raise HTTPException(status_code=400, detail=t_for(request, "tasks.errors.invalid_move"))

    await move_task(db, task, new_status, new_position)
    return JSONResponse({"ok": True})


@router.post("/{task_id}/delete")
async def task_delete(task_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    await require_admin(request, db)
    task = await _get_task_or_404(db, task_id, request)

    status_value, position = task.status, task.position
    await db.delete(task)
    await db.commit()
    await close_gap_after_delete(db, status_value, position)

    return RedirectResponse("/tasks/", status_code=302)
