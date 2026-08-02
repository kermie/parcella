"""Add category_id to invoice_line_items (issue #179)

Revision ID: 0073_invln_category
Revises: 0072_incoming_line_desc
Create Date: 2026-08-02

Needed for the cash-based accounting statement to attribute income by
category. Copied from InvoiceItemDefinition.category_id at finalize
time going forward -- there was never a link from InvoiceLineItem back
to its originating definition, so this cannot be backfilled for
invoices finalized before this migration.
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0073_invln_category"
down_revision: Union[str, None] = "0072_incoming_line_desc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "invoice_line_items",
        sa.Column(
            "category_id", sa.String(36),
            sa.ForeignKey("finance_categories.id", ondelete="SET NULL"), nullable=True,
        ),
    )
    op.create_index(
        "ix_invoice_line_items_category_id", "invoice_line_items", ["category_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_invoice_line_items_category_id", table_name="invoice_line_items")
    op.drop_column("invoice_line_items", "category_id")
