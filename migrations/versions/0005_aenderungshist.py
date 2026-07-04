"""Aenderungshistorie: generisches Audit-Log fuer Feldaenderungen

Revision ID: 0005_aenderungshistorie
Revises: 0004_patenschaft_optional
Create Date: 2026-07-05
"""
from typing import Union
from alembic import op
import sqlalchemy as sa

revision: str = "0005_aenderungshist"
down_revision: Union[str, None] = "0004_patenschaft_optional"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "aenderungshistorie",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("entitaet_typ", sa.String(50), nullable=False),
        sa.Column("entitaet_id", sa.String(36), nullable=False),
        sa.Column("feldname", sa.String(100), nullable=False),
        sa.Column("alter_wert", sa.Text(), nullable=True),
        sa.Column("neuer_wert", sa.Text(), nullable=True),
        sa.Column("geaendert_von_id", sa.String(36),
                  sa.ForeignKey("benutzer.id", ondelete="SET NULL"), nullable=True),
        sa.Column("geaendert_am", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_aenderungshistorie_entitaet_typ", "aenderungshistorie", ["entitaet_typ"])
    op.create_index("ix_aenderungshistorie_entitaet_id", "aenderungshistorie", ["entitaet_id"])
    op.create_index("ix_aenderungshistorie_geaendert_am", "aenderungshistorie", ["geaendert_am"])


def downgrade() -> None:
    op.drop_table("aenderungshistorie")
