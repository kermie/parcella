"""Add COMMUNAL value to parcelstatus enum (issue #168)

Revision ID: 0067_communal_parcel_status
Revises: 0066_public_burdens_pricing_mode
Create Date: 2026-07-31

New parcel status: a club-managed common area (paths, playground,
etc.) tracked as a real Parcel row -- its area_sqm counts toward the
club's total area but is excluded from Area A (leased parcels, see
app/area_utils.py's compute_area_a_sqm), unlike ACTIVE/TERMINATED
parcels. Freely switchable back to ACTIVE if the club leases it out.

SQLAlchemy's Enum column stores the Python enum MEMBER NAME (e.g.
"ACTIVE"), not its .value -- matching every other value already in
this type (see the enum-casing sharp edge in CLAUDE.md, and migrations
0052/0053/0063 for real past bugs from getting this backwards).
'COMMUNAL' below is deliberately uppercase to match the member name.
Postgres also requires ALTER TYPE ... ADD VALUE to run outside a
transaction block, hence the autocommit_block() below (same pattern as
0030/0052/0053/0066).
"""
from typing import Union

from alembic import op

revision: str = "0067_communal_parcel_status"
down_revision: Union[str, None] = "0066_public_burdens_pricing_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE parcelstatus ADD VALUE IF NOT EXISTS 'COMMUNAL'")


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE -- see 0030/0052/0053/0066's
    # downgrade notes for why this isn't implemented here.
    pass
