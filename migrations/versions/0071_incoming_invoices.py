"""Add incoming_invoices and incoming_invoice_line_items (issue #178)

Revision ID: 0071_incoming_invoices
Revises: 0070_account_transactions
Create Date: 2026-08-01

A bill the club received from a supplier/vendor -- the mirror image of
Invoice (which the club sends out), recorded directly by hand with one
or more categorized cost positions. The actual scanned bill, if any,
lives in a single shared cloud folder (ClubSetting
"incoming_invoices_cloud_folder"), never in this app's own database or
filesystem.
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0071_incoming_invoices"
down_revision: Union[str, None] = "0070_account_transactions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "incoming_invoices",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("sender", sa.String(255), nullable=False),
        sa.Column("invoice_number", sa.String(100), nullable=True),
        sa.Column("invoice_date", sa.Date(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("cloud_filename", sa.String(500), nullable=True),
        sa.Column(
            "created_by_id", sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "incoming_invoice_line_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "incoming_invoice_id", sa.String(36),
            sa.ForeignKey("incoming_invoices.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "category_id", sa.String(36),
            sa.ForeignKey("finance_categories.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_incoming_invoice_line_items_incoming_invoice_id",
        "incoming_invoice_line_items", ["incoming_invoice_id"],
    )
    op.create_index(
        "ix_incoming_invoice_line_items_category_id",
        "incoming_invoice_line_items", ["category_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_incoming_invoice_line_items_category_id", table_name="incoming_invoice_line_items")
    op.drop_index("ix_incoming_invoice_line_items_incoming_invoice_id", table_name="incoming_invoice_line_items")
    op.drop_table("incoming_invoice_line_items")
    op.drop_table("incoming_invoices")
