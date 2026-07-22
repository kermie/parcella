"""Restrict is_invoice_address to current tenants only

Revision ID: 0036_invoice_current_only
Revises: 0035_add_invoice_address
Create Date: 2026-07-22

Annual invoices must never go to a former tenant. Backfills any
existing former-tenant rows that were left flagged (e.g. the flag
wasn't cleared when a tenancy was ended before this migration existed),
then adds a CHECK constraint so the database itself refuses a row where
is_invoice_address is true and assigned_until is set.
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0036_invoice_current_only"
down_revision: Union[str, None] = "0035_add_invoice_address"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE member_parcels SET is_invoice_address = false "
        "WHERE assigned_until IS NOT NULL AND is_invoice_address"
    )
    op.create_check_constraint(
        "ck_invoice_address_only_for_current_tenants",
        "member_parcels",
        "NOT is_invoice_address OR assigned_until IS NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_invoice_address_only_for_current_tenants",
        "member_parcels",
        type_="check",
    )
