"""Add work_hours_shortfall value to invoicepricingmode enum (issue #83)

Revision ID: 0053_work_hours_shortfall
Revises: 0052_communal_area_share
Create Date: 2026-07-26

New pricing mode: charge whoever /work-hours/evaluation currently
shows owing a work-hours shortfall (see app/work_hours_evaluation.py),
automatically excluding anyone exempt or who already fulfilled their
hours -- fully computed, no manual scoping (see
app/invoice_generation.py).

SQLAlchemy's Enum column stores the Python enum MEMBER NAME (e.g.
"FIXED_PER_PARCEL"), not its .value ("fixed_per_parcel") -- matching
every other value already in this type. Postgres also requires
ALTER TYPE ... ADD VALUE to run outside a transaction block, hence the
autocommit_block() below (same pattern as 0030 and 0052).
"""
from typing import Union

from alembic import op

revision: str = "0053_work_hours_shortfall"
down_revision: Union[str, None] = "0052_communal_area_share"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE invoicepricingmode ADD VALUE IF NOT EXISTS 'WORK_HOURS_SHORTFALL'")


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE -- see 0030/0052's
    # downgrade notes for why this isn't implemented here.
    pass
