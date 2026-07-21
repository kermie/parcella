"""Sponsorship: mitglied_id made optional (nullable)

Revision ID: 0004_patenschaft_optional
Revises: 0003_erw_vorstand
Create Date: 2026-07-04
"""
from typing import Union
from alembic import op
import sqlalchemy as sa

revision: str = "0004_patenschaft_optional"
down_revision: Union[str, None] = "0003_erw_vorstand"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "patenschaften", "mitglied_id",
        existing_type=sa.String(36),
        nullable=True,
    )
    # Change the foreign key to SET NULL
    op.drop_constraint("patenschaften_mitglied_id_fkey", "patenschaften", type_="foreignkey")
    op.create_foreign_key(
        "patenschaften_mitglied_id_fkey",
        "patenschaften", "mitglieder",
        ["mitglied_id"], ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("patenschaften_mitglied_id_fkey", "patenschaften", type_="foreignkey")
    op.create_foreign_key(
        "patenschaften_mitglied_id_fkey",
        "patenschaften", "mitglieder",
        ["mitglied_id"], ["id"],
        ondelete="CASCADE",
    )
    op.alter_column(
        "patenschaften", "mitglied_id",
        existing_type=sa.String(36),
        nullable=False,
    )
