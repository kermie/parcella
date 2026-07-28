"""Add tasks.tags

Revision ID: 0056_task_tags
Revises: 0055_task_priority
Create Date: 2026-07-28

Issue #107: let a board/admin user tag a task board card with free-text
labels (e.g. "urgent", "outdoor"). A Postgres text array rather than a
join table to a separate Tag entity -- there's no cross-card vocabulary
to enforce or reuse-by-reference here (unlike, say, InventoryCategory),
just a per-card list of short strings. Not nullable: an untagged card is
an empty array, not a NULL, so callers never need a None check before
iterating.
"""
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0056_task_tags"
down_revision: Union[str, None] = "0055_task_priority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "tags", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}",
        ),
    )


def downgrade() -> None:
    op.drop_column("tasks", "tags")
