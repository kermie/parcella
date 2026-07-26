"""Add communal_area_share value to invoicepricingmode enum (issue #82)

Revision ID: 0052_communal_area_share
Revises: 0051_item_person_scope
Create Date: 2026-07-26

New pricing mode: "Share of the lease for the communal area" (Area B,
see app/area_utils.py). Splits Area B evenly across however many
parcels actually get billed for the item, so the sum of every tenant's
share reconstructs the whole communal area; the club still enters the
price per sqm by hand (see app/invoice_generation.py's
_communal_share_denominators / item_quantity_and_price).

Postgres requires ALTER TYPE ... ADD VALUE to run outside a
transaction block (a new enum value can't be used in the same
transaction that creates it), hence the autocommit_block() below.
"""
from typing import Union

from alembic import op

revision: str = "0052_communal_area_share"
down_revision: Union[str, None] = "0051_item_person_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLAlchemy's Enum column stores the Python enum MEMBER NAME (e.g.
    # "FIXED_PER_PARCEL"), not its .value ("fixed_per_parcel") -- matching
    # every other value already in this type (see 0040_annual_invoices).
    # COMMUNAL_AREA_SHARE must be added the same way, uppercase.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE invoicepricingmode ADD VALUE IF NOT EXISTS 'COMMUNAL_AREA_SHARE'")


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE. Removing an enum value
    # cleanly requires rebuilding the type (rename old, create new
    # without the value, cast the column, drop old) -- not implemented
    # here since nothing in this project relies on downgrading past
    # this point in normal operation. Any invoice_item_definitions or
    # invoice_item_templates row left with pricing_mode=
    # 'COMMUNAL_AREA_SHARE' would need to be changed to another mode by
    # hand before attempting a real downgrade.
    pass
