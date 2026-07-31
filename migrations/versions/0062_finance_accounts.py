"""Add finance_accounts and invoice_payments.account_id

Revision ID: 0062_finance_accounts
Revises: 0061_metering_price_config
Create Date: 2026-07-31

Issue #156: a club's real-world bank/cash accounts (e.g. an old and a
new giro account, plus a cash box). Purely a reporting tag on
InvoicePayment, same role FinanceCategory already has -- not a real
ledger, no manual transactions or opening balances.
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0062_finance_accounts"
down_revision: Union[str, None] = "0061_metering_price_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "finance_accounts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "account_type",
            sa.Enum("BANK", "CASH", name="financeaccounttype"),
            nullable=False,
        ),
        sa.Column("account_number", sa.String(100), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.add_column(
        "invoice_payments",
        sa.Column("account_id", sa.String(36), sa.ForeignKey("finance_accounts.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_invoice_payments_account_id", "invoice_payments", ["account_id"])


def downgrade() -> None:
    op.drop_index("ix_invoice_payments_account_id", table_name="invoice_payments")
    op.drop_column("invoice_payments", "account_id")

    op.drop_table("finance_accounts")
    op.execute("DROP TYPE IF EXISTS financeaccounttype")
