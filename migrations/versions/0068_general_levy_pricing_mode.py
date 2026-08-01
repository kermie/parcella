"""Add general_levy value to invoicepricingmode enum (issue #171)

Revision ID: 0068_general_levy_pricing_mode
Revises: 0067_communal_parcel_status
Create Date: 2026-08-01

New pricing mode: a general levy ("Umlage") -- a single total amount
the club needs to cover, split evenly across every billable parcel
(ACTIVE/TERMINATED with a current invoice-address resident -- same
denominator COMMUNAL_AREA_SHARE/PUBLIC_BURDENS already use, so the
entered total is always collected in full, never under-collected from
a vacant plot). See app/invoice_generation.py.

SQLAlchemy's Enum column stores the Python enum MEMBER NAME (e.g.
"ACTIVE"), not its .value -- matching every other value already in
this type (see the enum-casing sharp edge in CLAUDE.md, and migrations
0052/0053/0063/0066 for real past bugs from getting this backwards).
'GENERAL_LEVY' below is deliberately uppercase to match the member
name. Postgres also requires ALTER TYPE ... ADD VALUE to run outside a
transaction block, hence the autocommit_block() below (same pattern as
0030/0052/0053/0066/0067).
"""
from typing import Union

from alembic import op

revision: str = "0068_general_levy_pricing_mode"
down_revision: Union[str, None] = "0067_communal_parcel_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE invoicepricingmode ADD VALUE IF NOT EXISTS 'GENERAL_LEVY'")


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE -- see 0030/0052/0053/
    # 0066/0067's downgrade notes for why this isn't implemented here.
    pass
