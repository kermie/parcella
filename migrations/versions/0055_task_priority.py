"""Add tasks.priority

Revision ID: 0055_task_priority
Revises: 0054_task_lists
Create Date: 2026-07-28

Issue #106: let a board/admin user flag a task board card as
LOW/MEDIUM/HIGH priority. Nullable -- unset means "no priority", not a
default of MEDIUM, since most existing cards were created before this
feature existed and have no basis for a guessed priority.
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0055_task_priority"
down_revision: Union[str, None] = "0054_task_lists"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # CREATE TYPE explicitly first: op.add_column() with an inline sa.Enum()
    # only auto-creates the Postgres type as a side effect of op.create_table();
    # standalone add_column does not (see docs/module-tasks.md / migration
    # 0008_zaehlerwesen for the same pattern).
    op.execute("CREATE TYPE taskpriority AS ENUM ('LOW', 'MEDIUM', 'HIGH')")
    op.add_column(
        "tasks",
        sa.Column("priority", sa.Enum("LOW", "MEDIUM", "HIGH", name="taskpriority", create_type=False), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tasks", "priority")
    op.execute("DROP TYPE IF EXISTS taskpriority")
