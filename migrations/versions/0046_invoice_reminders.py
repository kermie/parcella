"""Add invoice_reminders

Revision ID: 0046_invoice_reminders
Revises: 0045_drop_run_starting_override
Create Date: 2026-07-25

Issue #59: dunning/collection reminders for unpaid outgoing invoices.
level is a plain incrementing counter (no fixed named stages), fee_amount
is optional and entered per reminder (not a club-wide default), and
delivery_method records how it actually went out -- resolved the same
way as the original invoice's own delivery (email if the recipient has
notifications + a stored address, otherwise a PDF for manual/postal
sending).
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0046_invoice_reminders"
down_revision: Union[str, None] = "0045_drop_run_starting_override"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invoice_reminders",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("invoice_id", sa.String(36), sa.ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("fee_amount", sa.Numeric(10, 2), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("delivery_method", sa.String(10), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_invoice_reminders_invoice_id", "invoice_reminders", ["invoice_id"])


def downgrade() -> None:
    op.drop_index("ix_invoice_reminders_invoice_id", table_name="invoice_reminders")
    op.drop_table("invoice_reminders")
