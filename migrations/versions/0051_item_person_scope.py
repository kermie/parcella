"""Split plot-scoped vs person-scoped invoice item targeting

Revision ID: 0051_item_person_scope
Revises: 0050_item_template_parcels
Create Date: 2026-07-26

Retires applies_to_members_without_parcel: FIXED_PER_PERSON items were
forced through the parcel-scoping machinery (residents-count on a
scoped parcel) plus a bolt-on flag for the no-parcel case, so a form
could nonsensically say "also bill members without a parcel" while
still showing a parcel picker. Gives FIXED_PER_PERSON its own
person-scope mirror of applies_to_all_parcels/parcel_scopes --
applies_to_all_members/member_scopes -- billed to targeted members
directly regardless of parcel status, never mixed with parcel
targeting again.

Data carry-over: any existing FIXED_PER_PERSON row with the old
applies_to_members_without_parcel=true had already opted into billing
members broadly, so it gets applies_to_all_members=true (an explicit
UPDATE, not just the new column's own default, so prior intent
actually carries over rather than being coincidental).
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0051_item_person_scope"
down_revision: Union[str, None] = "0050_item_template_parcels"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invoice_item_definition_members",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "invoice_item_definition_id", sa.String(36),
            sa.ForeignKey("invoice_item_definitions.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("member_id", sa.String(36), sa.ForeignKey("members.id", ondelete="CASCADE"), nullable=False),
    )
    op.create_index(
        "ix_invoice_item_definition_members_invoice_item_definition_id",
        "invoice_item_definition_members", ["invoice_item_definition_id"],
    )
    op.create_index(
        "ix_invoice_item_definition_members_member_id", "invoice_item_definition_members", ["member_id"],
    )
    op.create_unique_constraint(
        "uq_invoice_item_definition_member", "invoice_item_definition_members",
        ["invoice_item_definition_id", "member_id"],
    )

    op.create_table(
        "invoice_item_template_members",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "invoice_item_template_id", sa.String(36),
            sa.ForeignKey("invoice_item_templates.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("member_id", sa.String(36), sa.ForeignKey("members.id", ondelete="CASCADE"), nullable=False),
    )
    op.create_index(
        "ix_invoice_item_template_members_invoice_item_template_id",
        "invoice_item_template_members", ["invoice_item_template_id"],
    )
    op.create_index(
        "ix_invoice_item_template_members_member_id", "invoice_item_template_members", ["member_id"],
    )
    op.create_unique_constraint(
        "uq_invoice_item_template_member", "invoice_item_template_members",
        ["invoice_item_template_id", "member_id"],
    )

    op.add_column(
        "invoice_item_definitions",
        sa.Column("applies_to_all_members", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.add_column(
        "invoice_item_templates",
        sa.Column("applies_to_all_members", sa.Boolean(), nullable=False, server_default="true"),
    )

    op.execute(
        "UPDATE invoice_item_definitions SET applies_to_all_members = true "
        "WHERE pricing_mode = 'FIXED_PER_PERSON' AND applies_to_members_without_parcel = true"
    )
    op.execute(
        "UPDATE invoice_item_templates SET applies_to_all_members = true "
        "WHERE pricing_mode = 'FIXED_PER_PERSON' AND applies_to_members_without_parcel = true"
    )

    op.drop_column("invoice_item_definitions", "applies_to_members_without_parcel")
    op.drop_column("invoice_item_templates", "applies_to_members_without_parcel")


def downgrade() -> None:
    op.add_column(
        "invoice_item_definitions",
        sa.Column("applies_to_members_without_parcel", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "invoice_item_templates",
        sa.Column("applies_to_members_without_parcel", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.execute(
        "UPDATE invoice_item_definitions SET applies_to_members_without_parcel = true "
        "WHERE pricing_mode = 'FIXED_PER_PERSON' AND applies_to_all_members = true"
    )
    op.execute(
        "UPDATE invoice_item_templates SET applies_to_members_without_parcel = true "
        "WHERE pricing_mode = 'FIXED_PER_PERSON' AND applies_to_all_members = true"
    )

    op.drop_column("invoice_item_definitions", "applies_to_all_members")
    op.drop_column("invoice_item_templates", "applies_to_all_members")

    op.drop_index(
        "ix_invoice_item_template_members_member_id", table_name="invoice_item_template_members",
    )
    op.drop_index(
        "ix_invoice_item_template_members_invoice_item_template_id", table_name="invoice_item_template_members",
    )
    op.drop_table("invoice_item_template_members")

    op.drop_index(
        "ix_invoice_item_definition_members_member_id", table_name="invoice_item_definition_members",
    )
    op.drop_index(
        "ix_invoice_item_definition_members_invoice_item_definition_id",
        table_name="invoice_item_definition_members",
    )
    op.drop_table("invoice_item_definition_members")
