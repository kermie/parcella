"""Add counterparty to account_transactions (issue #185)

Revision ID: 0075_txn_counterparty
Revises: 0074_incoming_payments
Create Date: 2026-08-02

Lets a board member record who sent or received the money for a
manual or CSV-imported account booking -- "I want to know who send me
the money or who received this money from me."
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0075_txn_counterparty"
down_revision: Union[str, None] = "0074_incoming_payments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "account_transactions",
        sa.Column("counterparty", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("account_transactions", "counterparty")
