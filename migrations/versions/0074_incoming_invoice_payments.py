"""Add incoming_invoice_payments (issue #181)

Revision ID: 0074_incoming_payments
Revises: 0073_invln_category
Create Date: 2026-08-02

Lets a board member mark an incoming invoice as paid, same shape as
InvoicePayment for outgoing invoices (issue #181: "could be the same
method as in outgoing invoices").
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0074_incoming_payments"
down_revision: Union[str, None] = "0073_invln_category"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "incoming_invoice_payments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "incoming_invoice_id", sa.String(36),
            sa.ForeignKey("incoming_invoices.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("paid_on", sa.Date(), nullable=False),
        sa.Column("note", sa.String(255), nullable=True),
        sa.Column(
            "account_id", sa.String(36),
            sa.ForeignKey("finance_accounts.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "recorded_by_id", sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_incoming_invoice_payments_incoming_invoice_id",
        "incoming_invoice_payments", ["incoming_invoice_id"],
    )
    op.create_index(
        "ix_incoming_invoice_payments_account_id",
        "incoming_invoice_payments", ["account_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_incoming_invoice_payments_account_id", table_name="incoming_invoice_payments")
    op.drop_index("ix_incoming_invoice_payments_incoming_invoice_id", table_name="incoming_invoice_payments")
    op.drop_table("incoming_invoice_payments")
