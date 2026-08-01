"""Add account_transactions table (issue #174)

Revision ID: 0070_account_transactions
Revises: 0069_drop_invoice_address_check
Create Date: 2026-08-01

A manual or CSV-imported ledger entry against a FinanceAccount --
unlike InvoicePayment (always tied to a specific invoice), this covers
any other money movement on the account (a refund, a purchase, a bank
fee). Deliberately reopens FinanceAccount's original "not a ledger"
stance from issue #156 -- confirmed with the reporter; see ADR 0059.
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0070_account_transactions"
down_revision: Union[str, None] = "0069_drop_invoice_address_check"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "account_transactions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "account_id", sa.String(36),
            sa.ForeignKey("finance_accounts.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("booking_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source", sa.String(20), nullable=False, server_default="manual"),
        sa.Column(
            "recorded_by_id", sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_account_transactions_account_id", "account_transactions", ["account_id"])
    op.create_index("ix_account_transactions_booking_date", "account_transactions", ["booking_date"])


def downgrade() -> None:
    op.drop_index("ix_account_transactions_booking_date", table_name="account_transactions")
    op.drop_index("ix_account_transactions_account_id", table_name="account_transactions")
    op.drop_table("account_transactions")
