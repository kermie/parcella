"""Task board: multiple assignees per task (issue #109)

Revision ID: 0057_task_multiple_assignees
Revises: 0056_task_tags
Create Date: 2026-07-28

`tasks.assigned_to_id` was a single nullable FK to `users.id`. Issue #109
asked to allow more than one user on a card, so this replaces it with a
`task_assignees` join table (id/task_id/user_id/created_at), same shape as
`group_memberships` (see app/models.py's TaskAssignee docstring and ADR
0046).

Any existing `assigned_to_id` is carried over as a single row in the new
table before the column is dropped, using the id/insert-loop idiom from
0038_groups_and_permissions.py.

`task_id`/`user_id` are both ON DELETE CASCADE (unlike the old
`assigned_to_id`'s SET NULL) -- deleting a task should drop its assignment
rows, and deleting a user should drop their assignment rows too, same as
group_memberships.user_id; a user with real task-assignment history is
still blocked from a hard delete at the application layer (see
app/routers/admin.py's _USER_REFERENCE_CHECKS).

downgrade() is best-effort/lossy: a task with more than one assignee can
only carry one back into the old scalar column, so it picks the
earliest-assigned one and drops the rest.
"""
from typing import Union
import uuid

from alembic import op
import sqlalchemy as sa

revision: str = "0057_task_multiple_assignees"
down_revision: Union[str, None] = "0056_task_tags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_assignees",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("task_id", "user_id", name="uq_task_assignee"),
    )
    op.create_index("ix_task_assignees_task_id", "task_assignees", ["task_id"])
    op.create_index("ix_task_assignees_user_id", "task_assignees", ["user_id"])

    connection = op.get_bind()
    existing_assignments = connection.execute(
        sa.text("SELECT id, assigned_to_id FROM tasks WHERE assigned_to_id IS NOT NULL")
    ).fetchall()
    for task_id, user_id in existing_assignments:
        connection.execute(
            sa.text(
                "INSERT INTO task_assignees (id, task_id, user_id) VALUES (:id, :task_id, :user_id)"
            ),
            {"id": str(uuid.uuid4()), "task_id": task_id, "user_id": user_id},
        )

    op.drop_index("ix_tasks_assigned_to_id", table_name="tasks")
    op.drop_column("tasks", "assigned_to_id")


def downgrade() -> None:
    op.add_column("tasks", sa.Column("assigned_to_id", sa.String(36), nullable=True))
    op.create_foreign_key(
        "tasks_assigned_to_id_fkey", "tasks", "users", ["assigned_to_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_tasks_assigned_to_id", "tasks", ["assigned_to_id"])

    connection = op.get_bind()
    connection.execute(
        sa.text(
            "UPDATE tasks SET assigned_to_id = ("
            "  SELECT user_id FROM task_assignees"
            "  WHERE task_assignees.task_id = tasks.id"
            "  ORDER BY task_assignees.created_at ASC LIMIT 1"
            ")"
        )
    )

    op.drop_index("ix_task_assignees_user_id", table_name="task_assignees")
    op.drop_index("ix_task_assignees_task_id", table_name="task_assignees")
    op.drop_table("task_assignees")
