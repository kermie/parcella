"""Add task board module: general-purpose kanban (tasks table)

Revision ID: 0034_task_board
Revises: 0033_parcel_cloud_folders
Create Date: 2026-07-22

New module: a general club-business kanban board, separate from the
work-hours module's session-scoped WorkTask. Admin/board only. Fixed
three-column workflow (TODO/IN_PROGRESS/DONE) -- no per-club column
configuration in v1.

No changes to existing tables.
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0034_task_board"
down_revision: Union[str, None] = "0033_parcel_cloud_folders"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status", sa.Enum("TODO", "IN_PROGRESS", "DONE", name="taskstatus"),
            nullable=False, server_default="TODO",
        ),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column(
            "assigned_to_id", sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "created_by_id", sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_tasks_status", "tasks", ["status"])
    op.create_index("ix_tasks_assigned_to_id", "tasks", ["assigned_to_id"])


def downgrade() -> None:
    op.drop_index("ix_tasks_assigned_to_id", table_name="tasks")
    op.drop_index("ix_tasks_status", table_name="tasks")
    op.drop_table("tasks")
    sa.Enum(name="taskstatus").drop(op.get_bind(), checkfirst=True)
