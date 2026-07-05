"""Kuendigung_datum entfernen - wird durch Aenderungshistorie ersetzt

Revision ID: 0006_drop_kuendigung_dat
Revises: 0005_aenderungshist
Create Date: 2026-07-05
"""
from typing import Union
from alembic import op
import sqlalchemy as sa

revision: str = "0006_drop_kuendigung_dat"
down_revision: Union[str, None] = "0005_aenderungshist"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("parzellen", "kuendigung_datum")


def downgrade() -> None:
    op.add_column("parzellen", sa.Column("kuendigung_datum", sa.Date(), nullable=True))
