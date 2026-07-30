"""Metering: annual price per unit configuration (water/electricity usage billing)

Revision ID: 0061_metering_price_config
Revises: 0060_user_avatar
Create Date: 2026-07-31
"""
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0061_metering_price_config"
down_revision: Union[str, None] = "0060_user_avatar"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "metering_price_configuration",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "medium",
            postgresql.ENUM("WATER", "ELECTRICITY", name="meteringmedium", create_type=False),
            nullable=False,
        ),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("price_per_unit", sa.Numeric(8, 2), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("medium", "year", name="uq_metering_price_medium_year"),
    )


def downgrade() -> None:
    op.drop_table("metering_price_configuration")
