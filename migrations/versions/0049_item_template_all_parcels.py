"""Add invoice_item_templates.applies_to_all_parcels

Revision ID: 0049_item_template_all_parcels
Revises: 0048_member_invoices
Create Date: 2026-07-26

Fixes a real bug: items_add_from_catalog hardcoded
applies_to_all_parcels=True for every catalog-sourced item regardless
of the template's intent, so a fixed_per_person item meant to bill
ONLY members without a current parcel (applies_to_members_without_
parcel=True) also silently billed every parcel tenant, since the
catalog had no way to express "don't apply to parcels at all". Adds
the missing toggle to InvoiceItemTemplate, defaulting to True to match
the catalog's actual behavior before this column existed (every
existing template keeps applying to all parcels until someone
deliberately unchecks it).
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0049_item_template_all_parcels"
down_revision: Union[str, None] = "0048_member_invoices"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "invoice_item_templates",
        sa.Column("applies_to_all_parcels", sa.Boolean(), nullable=False, server_default="true"),
    )


def downgrade() -> None:
    op.drop_column("invoice_item_templates", "applies_to_all_parcels")
