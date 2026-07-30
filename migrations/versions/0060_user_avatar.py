"""Users: avatar_filename column (issue #150)

Revision ID: 0060_user_avatar
Revises: 0059_club_board_members
Create Date: 2026-07-30
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0060_user_avatar"
down_revision: Union[str, None] = "0059_club_board_members"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("avatar_filename", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "avatar_filename")
