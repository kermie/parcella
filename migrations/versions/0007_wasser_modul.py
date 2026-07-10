"""Wassermodul: Wasseranschluesse, Wasseruhren, Zaehlerstaende

Revision ID: 0007_wasser_modul
Revises: 0006_drop_kuendigung_dat
Create Date: 2026-07-06
"""
from typing import Union
from alembic import op
import sqlalchemy as sa

revision: str = "0007_wasser_modul"
down_revision: Union[str, None] = "0006_drop_kuendigung_dat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wasseranschluesse",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("typ", sa.Enum("HAUPTZAEHLER", "PARZELLE", "VEREIN", name="wasseranschlusstyp"), nullable=False),
        sa.Column("parzelle_id", sa.String(36), sa.ForeignKey("parzellen.id", ondelete="SET NULL"), nullable=True),
        sa.Column("bezeichnung", sa.String(255), nullable=True),
        sa.Column("notizen", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_wasseranschluesse_parzelle_id", "wasseranschluesse", ["parzelle_id"])

    op.create_table(
        "wasseruhren",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("anschluss_id", sa.String(36),
                  sa.ForeignKey("wasseranschluesse.id", ondelete="CASCADE"), nullable=False),
        sa.Column("nummer", sa.String(50), nullable=False, unique=True),
        sa.Column("ist_aktiv", sa.Boolean(), nullable=False),
        sa.Column("geeicht_bis", sa.Integer(), nullable=True),
        sa.Column("eingebaut_am", sa.Date(), nullable=True),
        sa.Column("ausgebaut_am", sa.Date(), nullable=True),
        sa.Column("anfangsstand", sa.Numeric(10, 1), nullable=False),
        sa.Column("notizen", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_wasseruhren_anschluss_id", "wasseruhren", ["anschluss_id"])
    op.create_index("ix_wasseruhren_nummer", "wasseruhren", ["nummer"])

    op.create_table(
        "zaehlerstaende",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("wasseruhr_id", sa.String(36),
                  sa.ForeignKey("wasseruhren.id", ondelete="CASCADE"), nullable=False),
        sa.Column("jahr", sa.Integer(), nullable=False),
        sa.Column("datum", sa.Date(), nullable=False),
        sa.Column("stand", sa.Numeric(10, 1), nullable=False),
        sa.Column("erfasst_von_id", sa.String(36),
                  sa.ForeignKey("benutzer.id", ondelete="SET NULL"), nullable=True),
        sa.Column("notiz", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("wasseruhr_id", "jahr", name="uq_wasseruhr_jahr"),
    )
    op.create_index("ix_zaehlerstaende_wasseruhr_id", "zaehlerstaende", ["wasseruhr_id"])


def downgrade() -> None:
    op.drop_table("zaehlerstaende")
    op.drop_table("wasseruhren")
    op.drop_table("wasseranschluesse")
    sa.Enum(name="wasseranschlusstyp").drop(op.get_bind(), checkfirst=True)
