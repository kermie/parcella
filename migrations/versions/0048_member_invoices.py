"""Add member invoices (fixed-per-person items for members without a parcel)

Revision ID: 0048_member_invoices
Revises: 0047_invoice_item_templates
Create Date: 2026-07-26

Invoicing was entirely parcel-first: every invoice belonged to exactly
one parcel, so a club member with no current parcel assignment (e.g. a
supporting member without a plot) could never be billed, regardless of
pricing mode. Makes invoices.parcel_id nullable and adds a nullable
member_id, with a CHECK constraint requiring exactly one of the two --
a "member invoice" is a second, parallel invoiceable subject alongside
the existing parcel invoice, not a replacement. Also adds
applies_to_members_without_parcel to invoice_item_definitions/
invoice_item_templates: an explicit, default-off per-item opt-in
(only meaningful for fixed_per_person items) so upgrading never
silently changes what an existing run bills.
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0048_member_invoices"
down_revision: Union[str, None] = "0047_invoice_item_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("invoices", "parcel_id", existing_type=sa.String(36), nullable=True)
    op.add_column(
        "invoices",
        sa.Column("member_id", sa.String(36), sa.ForeignKey("members.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_invoices_member_id", "invoices", ["member_id"])
    op.create_check_constraint(
        "ck_invoice_exactly_one_subject",
        "invoices",
        "(parcel_id IS NOT NULL AND member_id IS NULL) OR (parcel_id IS NULL AND member_id IS NOT NULL)",
    )

    op.add_column(
        "invoice_item_definitions",
        sa.Column("applies_to_members_without_parcel", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "invoice_item_templates",
        sa.Column("applies_to_members_without_parcel", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("invoice_item_templates", "applies_to_members_without_parcel")
    op.drop_column("invoice_item_definitions", "applies_to_members_without_parcel")

    op.drop_constraint("ck_invoice_exactly_one_subject", "invoices", type_="check")
    op.drop_index("ix_invoices_member_id", table_name="invoices")
    op.drop_column("invoices", "member_id")
    op.alter_column("invoices", "parcel_id", existing_type=sa.String(36), nullable=False)
