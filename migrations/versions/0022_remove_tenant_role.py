"""Remove primary/co-tenant role distinction from member_parcels

Revision ID: 0022_remove_tenant_role
Revises: 0021_fix_timestamp_defaults
Create Date: 2026-07-16

The board decided the primary/co-tenant distinction doesn't reflect how
the club actually treats liability: everyone assigned to a parcel is
held responsible together, regardless of who signed first. This drops
`is_primary_tenant` from `member_parcels`.

Insurance auto-coverage logic (previously anchored on "same address as
the primary tenant") was reworked in app/insurance_utils.py to group
current residents by matching address to each other instead -- the
largest address-sharing group becomes the auto-covered household, no
designated "primary" person needed.
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0022_remove_tenant_role"
down_revision: Union[str, None] = "0021_fix_timestamp_defaults"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("member_parcels", "is_primary_tenant")


def downgrade() -> None:
    op.add_column(
        "member_parcels",
        sa.Column("is_primary_tenant", sa.Boolean(), nullable=False, server_default="true"),
    )
