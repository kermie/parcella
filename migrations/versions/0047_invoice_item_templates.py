"""Add invoice_item_templates

Revision ID: 0047_invoice_item_templates
Revises: 0046_invoice_reminders
Create Date: 2026-07-26

Reusable catalog of billable line items (membership fee, water usage,
etc.), replacing the "copy items from another run" mechanism (issue
#66) with a curated, visible list a board member manages directly --
see app/models.py's InvoiceItemTemplate docstring. Reuses the existing
invoicepricingmode enum type (create_type=False) rather than
recreating it, since InvoiceItemDefinition already defined it.
"""
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0047_invoice_item_templates"
down_revision: Union[str, None] = "0046_invoice_reminders"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invoice_item_templates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("order_number", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "pricing_mode",
            postgresql.ENUM(
                "FIXED_PER_PARCEL", "FIXED_PER_PERSON", "PER_SQM",
                "WATER_USAGE", "ELECTRICITY_USAGE", "INSURANCE_COST",
                name="invoicepricingmode", create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("unit_price", sa.Numeric(10, 2), nullable=True),
        sa.Column("category_id", sa.String(36), sa.ForeignKey("finance_categories.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_invoice_item_templates_category_id", "invoice_item_templates", ["category_id"])


def downgrade() -> None:
    op.drop_index("ix_invoice_item_templates_category_id", table_name="invoice_item_templates")
    op.drop_table("invoice_item_templates")
