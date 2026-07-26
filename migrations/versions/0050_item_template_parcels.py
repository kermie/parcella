"""Add invoice_item_template_parcels

Revision ID: 0050_item_template_parcels
Revises: 0049_item_template_all_parcels
Create Date: 2026-07-26

Explicit request: the specific-parcel picker (already added to a
run's own items) is also needed on item catalog templates -- mirrors
invoice_item_definition_parcels for InvoiceItemTemplate.
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0050_item_template_parcels"
down_revision: Union[str, None] = "0049_item_template_all_parcels"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invoice_item_template_parcels",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "invoice_item_template_id", sa.String(36),
            sa.ForeignKey("invoice_item_templates.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("parcel_id", sa.String(36), sa.ForeignKey("parcels.id", ondelete="CASCADE"), nullable=False),
    )
    op.create_index(
        "ix_invoice_item_template_parcels_invoice_item_template_id",
        "invoice_item_template_parcels", ["invoice_item_template_id"],
    )
    op.create_index(
        "ix_invoice_item_template_parcels_parcel_id", "invoice_item_template_parcels", ["parcel_id"],
    )
    op.create_unique_constraint(
        "uq_invoice_item_template_parcel", "invoice_item_template_parcels",
        ["invoice_item_template_id", "parcel_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_invoice_item_template_parcels_parcel_id", table_name="invoice_item_template_parcels",
    )
    op.drop_index(
        "ix_invoice_item_template_parcels_invoice_item_template_id", table_name="invoice_item_template_parcels",
    )
    op.drop_table("invoice_item_template_parcels")
