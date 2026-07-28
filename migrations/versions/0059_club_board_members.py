"""Club settings: board members (issue #111)

Revision ID: 0059_club_board_members
Revises: 0058_task_comments
Create Date: 2026-07-28
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0059_club_board_members"
down_revision: Union[str, None] = "0058_task_comments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "club_board_members",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("member_id", sa.String(36), sa.ForeignKey("members.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("member_id", name="uq_club_board_member"),
    )
    op.create_index("ix_club_board_members_member_id", "club_board_members", ["member_id"])


def downgrade() -> None:
    op.drop_index("ix_club_board_members_member_id", table_name="club_board_members")
    op.drop_table("club_board_members")
