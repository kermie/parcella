"""Finances module: annual invoices (issues #55/#56/#57/#58)

Revision ID: 0040_annual_invoices
Revises: 0039_group_access_flags
Create Date: 2026-07-24

New tables for the Finances module's first feature, annual invoices:
- invoice_runs: one batch per year ("Annual invoices 2026").
- invoice_item_definitions (+ invoice_item_definition_parcels for
  explicit parcel scoping): the line-item types a council member
  configures for a run before generating invoices.
- invoices: one per included parcel, generated from a DRAFT run.
  Recipient name/address are snapshotted at generation time.
- invoice_line_items: the priced lines on a generated invoice.
- invoice_payments: (possibly partial) payments recorded against an
  invoice -- payment status is derived from these, not stored.

No data migration -- this is a wholly new feature area, nothing to
backfill.
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0040_annual_invoices"
down_revision: Union[str, None] = "0039_group_access_flags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invoice_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("issued_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("footer_text", sa.Text(), nullable=True),
        sa.Column(
            "status", sa.Enum("DRAFT", "FINALIZED", name="invoicerunstatus"),
            nullable=False, server_default="DRAFT",
        ),
        sa.Column("created_by_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_invoice_runs_year", "invoice_runs", ["year"])
    op.create_index("ix_invoice_runs_status", "invoice_runs", ["status"])

    op.create_table(
        "invoice_item_definitions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("invoice_run_id", sa.String(36), sa.ForeignKey("invoice_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("order_number", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "pricing_mode",
            sa.Enum(
                "FIXED_PER_PARCEL", "FIXED_PER_PERSON", "PER_SQM",
                "WATER_USAGE", "ELECTRICITY_USAGE", "INSURANCE_COST",
                name="invoicepricingmode",
            ),
            nullable=False,
        ),
        sa.Column("unit_price", sa.Numeric(10, 2), nullable=True),
        sa.Column("applies_to_all_parcels", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.create_index("ix_invoice_item_definitions_invoice_run_id", "invoice_item_definitions", ["invoice_run_id"])

    op.create_table(
        "invoice_item_definition_parcels",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "invoice_item_definition_id", sa.String(36),
            sa.ForeignKey("invoice_item_definitions.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("parcel_id", sa.String(36), sa.ForeignKey("parcels.id", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("invoice_item_definition_id", "parcel_id", name="uq_invoice_item_definition_parcel"),
    )
    op.create_index(
        "ix_invoice_item_definition_parcels_definition_id",
        "invoice_item_definition_parcels", ["invoice_item_definition_id"],
    )
    op.create_index("ix_invoice_item_definition_parcels_parcel_id", "invoice_item_definition_parcels", ["parcel_id"])

    op.create_table(
        "invoices",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("invoice_run_id", sa.String(36), sa.ForeignKey("invoice_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parcel_id", sa.String(36), sa.ForeignKey("parcels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("invoice_number", sa.String(20), nullable=False, unique=True),
        sa.Column("recipient_names", sa.Text(), nullable=False),
        sa.Column("recipient_address", sa.Text(), nullable=False),
        sa.Column("subtotal", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("pdf_generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("emailed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("printed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("uploaded_to_cloud_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_invoices_invoice_run_id", "invoices", ["invoice_run_id"])
    op.create_index("ix_invoices_parcel_id", "invoices", ["parcel_id"])
    op.create_index("ix_invoices_invoice_number", "invoices", ["invoice_number"], unique=True)

    op.create_table(
        "invoice_line_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("invoice_id", sa.String(36), sa.ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("order_number", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("quantity", sa.Numeric(10, 2), nullable=False, server_default="1"),
        sa.Column("unit_price", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("line_total", sa.Numeric(10, 2), nullable=False, server_default="0"),
    )
    op.create_index("ix_invoice_line_items_invoice_id", "invoice_line_items", ["invoice_id"])

    op.create_table(
        "invoice_payments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("invoice_id", sa.String(36), sa.ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("paid_on", sa.Date(), nullable=False),
        sa.Column("note", sa.String(255), nullable=True),
        sa.Column("recorded_by_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_invoice_payments_invoice_id", "invoice_payments", ["invoice_id"])


def downgrade() -> None:
    op.drop_index("ix_invoice_payments_invoice_id", table_name="invoice_payments")
    op.drop_table("invoice_payments")

    op.drop_index("ix_invoice_line_items_invoice_id", table_name="invoice_line_items")
    op.drop_table("invoice_line_items")

    op.drop_index("ix_invoices_invoice_number", table_name="invoices")
    op.drop_index("ix_invoices_parcel_id", table_name="invoices")
    op.drop_index("ix_invoices_invoice_run_id", table_name="invoices")
    op.drop_table("invoices")

    op.drop_index("ix_invoice_item_definition_parcels_parcel_id", table_name="invoice_item_definition_parcels")
    op.drop_index("ix_invoice_item_definition_parcels_definition_id", table_name="invoice_item_definition_parcels")
    op.drop_table("invoice_item_definition_parcels")

    op.drop_index("ix_invoice_item_definitions_invoice_run_id", table_name="invoice_item_definitions")
    op.drop_table("invoice_item_definitions")

    op.drop_index("ix_invoice_runs_status", table_name="invoice_runs")
    op.drop_index("ix_invoice_runs_year", table_name="invoice_runs")
    op.drop_table("invoice_runs")

    op.execute("DROP TYPE IF EXISTS invoicepricingmode")
    op.execute("DROP TYPE IF EXISTS invoicerunstatus")
