"""Task board: comments on a card (issue #108)

Revision ID: 0058_task_comments
Revises: 0057_task_multiple_assignees
Create Date: 2026-07-28

Adds a `task_comments` table -- a simple append-only comment thread per
task, admin/board-only like the rest of the task board module. Modeled
after `TicketMessage` but without the email/direction/HTML-sanitization
concerns that don't apply here: plain-text content, add/delete only, no
edit. `task_id` cascades on delete (a comment can't outlive its task),
mirroring `ticket_messages.ticket_id`.
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0058_task_comments"
down_revision: Union[str, None] = "0057_task_multiple_assignees"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_comments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_by_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_task_comments_task_id", "task_comments", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_task_comments_task_id", table_name="task_comments")
    op.drop_table("task_comments")
