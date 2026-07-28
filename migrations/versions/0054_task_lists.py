"""Task board: configurable lists replace the fixed status enum (issue #100)

Revision ID: 0054_task_lists
Revises: 0053_work_hours_shortfall
Create Date: 2026-07-28

ADR 0034 shipped the task board with a fixed three-column workflow
(TODO/IN_PROGRESS/DONE) and explicitly deferred configurable columns as
a bigger feature. Issue #100 asked for exactly that, so this migration
replaces the `TaskStatus` enum column with a `task_lists` table (see
ADR 0043 and docs/module-tasks.md) -- id/name/position, same shape as
`tasks.position` itself.

Seeds three default lists ("To Do"/"In Progress"/"Done", in English --
list names are free text from here on, not i18n keys, same as
InventoryCategory.name) and backfills every existing task's new
`list_id` from its old `status`, using the id/insert idiom from
0038_groups_and_permissions.py. Existing `position` values are already
gapless per old status and carry over unchanged.

`tasks.list_id` has a plain (RESTRICT) FK to `task_lists.id`, not
ON DELETE SET NULL like InventoryCategory's items -- a kanban card must
always be in a visible column, so list deletion is handled entirely at
the application layer (app/task_board.py's delete_list(), which
reassigns a list's cards to another list before removing the row).

downgrade() is best-effort/lossy: it maps task_lists rows back to
TODO/IN_PROGRESS/DONE by name, which is only correct if those three
default lists still exist, unrenamed, and no other lists were created.
"""
from typing import Union
import uuid

from alembic import op
import sqlalchemy as sa

revision: str = "0054_task_lists"
down_revision: Union[str, None] = "0053_work_hours_shortfall"
branch_labels = None
depends_on = None

DEFAULT_LISTS = ["To Do", "In Progress", "Done"]
STATUS_TO_NAME = {"TODO": "To Do", "IN_PROGRESS": "In Progress", "DONE": "Done"}


def upgrade() -> None:
    op.create_table(
        "task_lists",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    connection = op.get_bind()

    name_to_id = {}
    for position, name in enumerate(DEFAULT_LISTS):
        list_id = str(uuid.uuid4())
        name_to_id[name] = list_id
        connection.execute(
            sa.text("INSERT INTO task_lists (id, name, position) VALUES (:id, :name, :position)"),
            {"id": list_id, "name": name, "position": position},
        )

    op.add_column("tasks", sa.Column("list_id", sa.String(36), nullable=True))

    for status, name in STATUS_TO_NAME.items():
        connection.execute(
            sa.text("UPDATE tasks SET list_id = :list_id WHERE status = :status"),
            {"list_id": name_to_id[name], "status": status},
        )

    op.alter_column("tasks", "list_id", nullable=False)
    op.create_foreign_key("fk_tasks_list_id", "tasks", "task_lists", ["list_id"], ["id"])
    op.create_index("ix_tasks_list_id", "tasks", ["list_id"])

    op.drop_index("ix_tasks_status", table_name="tasks")
    op.drop_column("tasks", "status")
    sa.Enum(name="taskstatus").drop(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "status", sa.Enum("TODO", "IN_PROGRESS", "DONE", name="taskstatus"),
            nullable=True,
        ),
    )
    op.create_index("ix_tasks_status", "tasks", ["status"])

    connection = op.get_bind()
    for status, name in STATUS_TO_NAME.items():
        connection.execute(
            sa.text(
                "UPDATE tasks SET status = :status WHERE list_id = "
                "(SELECT id FROM task_lists WHERE name = :name LIMIT 1)"
            ),
            {"status": status, "name": name},
        )
    # Any task whose list was renamed/added beyond the three defaults has
    # no status mapping -- falls back to TODO rather than leaving NULL,
    # since the column bears NOT NULL below (see module docstring: this
    # whole downgrade path is best-effort/lossy).
    connection.execute(sa.text("UPDATE tasks SET status = 'TODO' WHERE status IS NULL"))
    op.alter_column("tasks", "status", nullable=False, server_default="TODO")

    op.drop_constraint("fk_tasks_list_id", "tasks", type_="foreignkey")
    op.drop_index("ix_tasks_list_id", table_name="tasks")
    op.drop_column("tasks", "list_id")
    op.drop_table("task_lists")
