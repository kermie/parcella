"""Add public_burdens value to invoicepricingmode enum (issue #163)

Revision ID: 0066_public_burdens_pricing_mode
Revises: 0065_restore_metering_price
Create Date: 2026-07-31

New pricing mode: "Public burdens" ("öffentliche Lasten"), charged at
one rate per sqm against the parcel's own leased area PLUS its share
of the communal area (Area B) lease -- combining what PER_SQM and
COMMUNAL_AREA_SHARE each do separately into a single billed quantity
(see app/invoice_generation.py).

SQLAlchemy's Enum column stores the Python enum MEMBER NAME (e.g.
"FIXED_PER_PARCEL"), not its .value ("fixed_per_parcel") -- matching
every other value already in this type. Getting this backwards was a
real, confirmed production bug for COMMUNAL_AREA_SHARE (fixed in
0063) -- 'PUBLIC_BURDENS' below is deliberately uppercase to match the
member name, not the lowercase .value. Postgres also requires
ALTER TYPE ... ADD VALUE to run outside a transaction block, hence the
autocommit_block() below (same pattern as 0030/0052/0053).
"""
from typing import Union

from alembic import op

revision: str = "0066_public_burdens_pricing_mode"
down_revision: Union[str, None] = "0065_restore_metering_price"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE invoicepricingmode ADD VALUE IF NOT EXISTS 'PUBLIC_BURDENS'")


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE -- see 0030/0052/0053's
    # downgrade notes for why this isn't implemented here.
    pass
