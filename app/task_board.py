"""
Card-ordering logic for the task board module (see app/models.py for
the Task/TaskStatus model and the reasoning behind the position field).

Shared between the web router (app/routers/tasks.py) and the REST API
(app/routers/api_tasks.py) so both move cards with identical semantics.
"""
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Task, TaskStatus


async def next_position(db: AsyncSession, status: TaskStatus) -> int:
    """Position for a brand-new card: appended to the end of its column."""
    result = await db.execute(select(Task).where(Task.status == status))
    return len(result.scalars().all())


async def move_task(db: AsyncSession, task: Task, new_status: TaskStatus, new_position: int) -> None:
    """
    Moves a card to `new_status` at `new_position` (0-based index within
    that column), and fully renumbers the affected column(s) so
    `position` stays a gapless 0..n-1 sequence.

    Works for both cross-column moves and pure reordering within the
    same column: the target column's cards (excluding this one) are
    fetched, the card is reinserted at the clamped target index, and
    the whole column is renumbered in one pass.
    """
    old_status = task.status

    if old_status != new_status:
        result = await db.execute(
            select(Task).where(Task.status == old_status, Task.id != task.id).order_by(Task.position)
        )
        for index, other in enumerate(result.scalars().all()):
            other.position = index

    result = await db.execute(
        select(Task).where(Task.status == new_status, Task.id != task.id).order_by(Task.position)
    )
    column = result.scalars().all()
    clamped = max(0, min(new_position, len(column)))
    column.insert(clamped, task)

    for index, card in enumerate(column):
        card.position = index
        card.status = new_status

    await db.commit()


async def close_gap_after_delete(db: AsyncSession, status: TaskStatus, deleted_position: int) -> None:
    """Renumbers a column after a card is removed from it, so
    `position` stays gapless."""
    result = await db.execute(
        select(Task).where(Task.status == status, Task.position > deleted_position).order_by(Task.position)
    )
    for card in result.scalars().all():
        card.position -= 1
    await db.commit()
