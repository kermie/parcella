"""Restore metering_price_configuration (feature un-reverted)

Revision ID: 0065_restore_metering_price
Revises: 0064_drop_metering_price_config
Create Date: 2026-07-31

The per-year/per-medium metering price configuration (0061, dropped by
0064 after a brief revert) is restored: the user confirmed they
actually want the price set once per year, with the water_usage/
electricity_usage item form's price field hidden and computed
automatically from here, same as insurance_cost/work_hours_shortfall.
See docs/ADR/0056's second Update note. Identical shape to 0061.
"""
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0065_restore_metering_price"
down_revision: Union[str, None] = "0064_drop_metering_price_config"
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
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("medium", "year", name="uq_metering_price_medium_year"),
    )


def downgrade() -> None:
    op.drop_table("metering_price_configuration")
