"""Drop metering_price_configuration (feature reverted)

Revision ID: 0064_drop_metering_price_config
Revises: 0063_fix_communal_share_enum
Create Date: 2026-07-31

The per-year/per-medium metering price configuration (0061) was
reverted: a club's utility tariff changes from one invoice run to the
next, so the price per unit stays a manually-typed field on the item
(app/invoice_generation.py), not a stored setting. Drops the now-
model-less table only -- its `medium` column reused the existing
`meteringmedium` enum type (still in active use by `metering_points`),
so that type itself is left alone.
"""
from typing import Union

from alembic import op

revision: str = "0064_drop_metering_price_config"
down_revision: Union[str, None] = "0063_fix_communal_share_enum"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("metering_price_configuration")


def downgrade() -> None:
    # Not implemented -- the feature was deliberately removed, nothing
    # in current data relies on it existing again. See migration 0061
    # for the original create_table if this table shape is ever needed.
    pass
