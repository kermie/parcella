"""Add description to incoming_invoice_line_items

Revision ID: 0072_incoming_line_desc
Revises: 0071_incoming_invoices
Create Date: 2026-08-02

Each position on an incoming invoice needs to say what was actually
bought, not just which category/amount it falls under (e.g. three
positions in three different categories on one bill still each need
their own "what is this" text).
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0072_incoming_line_desc"
down_revision: Union[str, None] = "0071_incoming_invoices"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "incoming_invoice_line_items",
        sa.Column("description", sa.String(500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("incoming_invoice_line_items", "description")
