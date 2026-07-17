"""Add work_tasks table for the work-hours task-assignment feature

Revision ID: 0025_work_tasks
Revises: 0024_ticket_message_html
Create Date: 2026-07-18

New feature: a task backlog for the work-hours program. A task can
optionally be scheduled to a specific work session, and within that
session, optionally assigned to one specific signed-up participant --
so whoever coordinates a session can match tasks to people appropriately
(e.g. lighter tasks for members who can't do heavy physical work). The
app itself stores none of that reasoning -- just a "workload" label on
the task (light/moderate/demanding) and a coordinator's manual
assignment of task to participant.
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0025_work_tasks"
down_revision: Union[str, None] = "0024_ticket_message_html"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "work_tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "workload",
            sa.Enum("LIGHT", "MODERATE", "DEMANDING", name="taskworkload"),
            nullable=False,
            server_default="MODERATE",
        ),
        sa.Column(
            "session_id", sa.String(36),
            sa.ForeignKey("work_sessions.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "assigned_participation_id", sa.String(36),
            sa.ForeignKey("session_participations.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("is_done", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_by_id", sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_work_tasks_session_id", "work_tasks", ["session_id"])
    op.create_index("ix_work_tasks_assigned_participation_id", "work_tasks", ["assigned_participation_id"])


def downgrade() -> None:
    op.drop_index("ix_work_tasks_assigned_participation_id", table_name="work_tasks")
    op.drop_index("ix_work_tasks_session_id", table_name="work_tasks")
    op.drop_table("work_tasks")
    sa.Enum(name="taskworkload").drop(op.get_bind(), checkfirst=True)
