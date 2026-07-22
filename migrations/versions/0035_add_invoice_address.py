"""Add is_invoice_address to member_parcels

Revision ID: 0035_add_invoice_address
Revises: 0034_task_board
Create Date: 2026-07-22

A parcel can have several members with different snail-mail addresses.
This flag marks which assigned member's address is used as the parcel's
invoice address. Same shape as the old is_primary_tenant column (see
migration 0022_remove_tenant_role / ADR 0018), but a distinct concept:
it selects an address for billing, not a liability rank -- everyone
assigned to a parcel is still held jointly responsible regardless of
this flag.
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0035_add_invoice_address"
down_revision: Union[str, None] = "0034_task_board"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "member_parcels",
        sa.Column("is_invoice_address", sa.Boolean(), nullable=False, server_default="true"),
    )


def downgrade() -> None:
    op.drop_column("member_parcels", "is_invoice_address")
