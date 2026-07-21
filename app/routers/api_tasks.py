"""
API router: Task board -- full CRUD plus a dedicated move endpoint for
reordering/moving cards between columns. Admin/board only (require_admin_api),
matching the web UI's permission level.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import Task, TaskStatus, User
from app.api_auth import require_admin_api
from app.module_flags import require_module
from app.task_board import next_position, move_task, close_gap_after_delete
from app.schemas import KanbanTaskCreate, KanbanTaskUpdate, KanbanTaskMove, KanbanTaskOut

router = APIRouter(
    prefix="/api/v1/tasks",
    tags=["API: Task Board"],
    dependencies=[Depends(require_module("tasks"))],
)


async def _get_task_or_404(db: AsyncSession, task_id: str) -> Task:
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.get("", response_model=List[KanbanTaskOut], summary="List tasks")
async def tasks_list(
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by TODO, IN_PROGRESS or DONE"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin_api),
):
    query = select(Task).order_by(Task.status, Task.position)
    if status_filter:
        query = query.where(Task.status == TaskStatus(status_filter))
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{task_id}", response_model=KanbanTaskOut, summary="Retrieve a single task")
async def task_get(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin_api),
):
    return await _get_task_or_404(db, task_id)


@router.post("", response_model=KanbanTaskOut, status_code=status.HTTP_201_CREATED, summary="Create a task")
async def task_create(
    data: KanbanTaskCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin_api),
):
    task_status = TaskStatus(data.status)
    task = Task(
        title=data.title,
        description=data.description,
        due_date=data.due_date,
        assigned_to_id=data.assigned_to_id,
        status=task_status,
        position=await next_position(db, task_status),
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


@router.put("/{task_id}", response_model=KanbanTaskOut, summary="Update a task")
async def task_update(
    task_id: str,
    data: KanbanTaskUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin_api),
):
    task = await _get_task_or_404(db, task_id)

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(task, field, value)

    await db.commit()
    await db.refresh(task)
    return task


@router.post(
    "/{task_id}/move", response_model=KanbanTaskOut, summary="Move a task",
    description="Moves a task to a column (status) and position. Renumbers "
                "the affected column(s) so `position` stays gapless.",
)
async def task_move(
    task_id: str,
    data: KanbanTaskMove,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin_api),
):
    task = await _get_task_or_404(db, task_id)
    await move_task(db, task, TaskStatus(data.status), data.position)
    await db.refresh(task)
    return task


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a task")
async def task_delete(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin_api),
):
    task = await _get_task_or_404(db, task_id)
    task_status, position = task.status, task.position
    await db.delete(task)
    await db.commit()
    await close_gap_after_delete(db, task_status, position)
