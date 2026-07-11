"""Einkaufswuensche: Vier-Augen-Prinzip fuer Vereinsausgaben

Revision ID: 0013_einkaufswuensche
Revises: 0012_spam_begruendung
Create Date: 2026-07-12
"""
from typing import Union
from alembic import op
import sqlalchemy as sa

revision: str = "0013_einkaufswuensche"
down_revision: Union[str, None] = "0012_spam_begruendung"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "einkaufswuensche",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("titel", sa.String(255), nullable=False),
        sa.Column("begruendung", sa.Text(), nullable=False),
        sa.Column("link", sa.String(500), nullable=True),
        sa.Column("geschaetzte_kosten_eur", sa.Numeric(10, 2), nullable=True),
        sa.Column("status", sa.Enum("OFFEN", "GENEHMIGT", "ABGELEHNT", name="einkaufswunschstatus"), nullable=False),
        sa.Column("angefragt_von_id", sa.String(36), sa.ForeignKey("benutzer.id", ondelete="SET NULL"), nullable=True),
        sa.Column("anfragender_name", sa.String(255), nullable=True),
        sa.Column("anfragender_email", sa.String(255), nullable=True),
        sa.Column("erstellt_von_id", sa.String(36), sa.ForeignKey("benutzer.id", ondelete="SET NULL"), nullable=True),
        sa.Column("bestaetigungs_token", sa.String(255), nullable=True, unique=True),
        sa.Column("vom_anfragenden_bestaetigt", sa.Boolean(), nullable=False),
        sa.Column("vom_anfragenden_bestaetigt_am", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ablehnungsgrund", sa.Text(), nullable=True),
        sa.Column("abgelehnt_von_id", sa.String(36), sa.ForeignKey("benutzer.id", ondelete="SET NULL"), nullable=True),
        sa.Column("abgelehnt_am", sa.DateTime(timezone=True), nullable=True),
        sa.Column("genehmigt_am", sa.DateTime(timezone=True), nullable=True),
        sa.Column("erstellt_am", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("aktualisiert_am", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_einkaufswuensche_status", "einkaufswuensche", ["status"])

    op.create_table(
        "einkaufswunsch_freigaben",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("einkaufswunsch_id", sa.String(36),
                  sa.ForeignKey("einkaufswuensche.id", ondelete="CASCADE"), nullable=False),
        sa.Column("benutzer_id", sa.String(36),
                  sa.ForeignKey("benutzer.id", ondelete="CASCADE"), nullable=False),
        sa.Column("freigegeben_am", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("einkaufswunsch_id", "benutzer_id", name="uq_einkaufswunsch_freigabe"),
    )
    op.create_index("ix_einkaufswunsch_freigaben_ew_id", "einkaufswunsch_freigaben", ["einkaufswunsch_id"])
    op.create_index("ix_einkaufswunsch_freigaben_benutzer_id", "einkaufswunsch_freigaben", ["benutzer_id"])


def downgrade() -> None:
    op.drop_table("einkaufswunsch_freigaben")
    op.drop_table("einkaufswuensche")
    sa.Enum(name="einkaufswunschstatus").drop(op.get_bind(), checkfirst=True)
