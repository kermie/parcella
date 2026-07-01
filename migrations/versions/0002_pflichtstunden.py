"""Pflichtstunden-System: Konfiguration, Vereinsrollen, Patenschaften, Arbeitseinsätze

Revision ID: 0002_pflichtstunden
Revises: 0001_initial
Create Date: 2026-07-01
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0002_pflichtstunden"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    # Pflichtstunden-Konfiguration
    op.create_table(
        "pflichtstunden_konfiguration",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("jahr", sa.Integer(), nullable=False),
        sa.Column("stunden_gesamt", sa.Numeric(5, 1), nullable=False),
        sa.Column("stundensatz_eur", sa.Numeric(8, 2), nullable=False),
        sa.Column("modus", sa.Enum("PRO_PACHTVERTRAG", "PRO_MITGLIED",
                                    name="pflichtstundenmodus"), nullable=False),
        sa.Column("notiz", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_pflichtstunden_konfiguration_jahr", "pflichtstunden_konfiguration", ["jahr"], unique=True)

    # Vereinsrollen
    op.create_table(
        "vereinsrollen",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("beschreibung", sa.Text(), nullable=True),
        sa.Column("pflichtstunden_befreit", sa.Boolean(), nullable=False),
        sa.Column("befreiungsgrund", sa.Enum("VORSTAND", "KRANKHEIT", "ALTER", "SONSTIG",
                                              name="befreiungsgrund"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # Mitglied → Vereinsrolle (jahresbasiert)
    op.create_table(
        "mitglied_vereinsrolle",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("mitglied_id", sa.String(36),
                  sa.ForeignKey("mitglieder.id", ondelete="CASCADE"), nullable=False),
        sa.Column("vereinsrolle_id", sa.String(36),
                  sa.ForeignKey("vereinsrollen.id", ondelete="CASCADE"), nullable=False),
        sa.Column("jahr", sa.Integer(), nullable=False),
        sa.Column("von", sa.Date(), nullable=True),
        sa.Column("bis", sa.Date(), nullable=True),
        sa.Column("notiz", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("mitglied_id", "vereinsrolle_id", "jahr",
                            name="uq_mitglied_vereinsrolle_jahr"),
    )
    op.create_index("ix_mitglied_vereinsrolle_mitglied_id", "mitglied_vereinsrolle", ["mitglied_id"])
    op.create_index("ix_mitglied_vereinsrolle_vereinsrolle_id", "mitglied_vereinsrolle", ["vereinsrolle_id"])

    # Patenschaften
    op.create_table(
        "patenschaften",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("mitglied_id", sa.String(36),
                  sa.ForeignKey("mitglieder.id", ondelete="CASCADE"), nullable=False),
        sa.Column("bereich", sa.String(255), nullable=False),
        sa.Column("beschreibung", sa.Text(), nullable=True),
        sa.Column("stunden_anrechenbar", sa.Numeric(5, 1), nullable=False),
        sa.Column("von", sa.Date(), nullable=False),
        sa.Column("bis", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_patenschaften_mitglied_id", "patenschaften", ["mitglied_id"])

    # Arbeitseinsätze
    op.create_table(
        "arbeitseinsaetze",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("titel", sa.String(255), nullable=False),
        sa.Column("beschreibung", sa.Text(), nullable=True),
        sa.Column("typ", sa.Enum("STANDARD", "BESONDERS", name="einsatztyp"), nullable=False),
        sa.Column("datum", sa.Date(), nullable=False),
        sa.Column("uhrzeit_von", sa.String(5), nullable=True),
        sa.Column("uhrzeit_bis", sa.String(5), nullable=True),
        sa.Column("max_teilnehmer", sa.Integer(), nullable=True),
        sa.Column("stunden_pro_teilnehmer", sa.Numeric(4, 1), nullable=True),
        sa.Column("erstellt_von_id", sa.String(36),
                  sa.ForeignKey("benutzer.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_arbeitseinsaetze_datum", "arbeitseinsaetze", ["datum"])

    # Einsatz-Teilnahmen
    op.create_table(
        "einsatz_teilnahmen",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("einsatz_id", sa.String(36),
                  sa.ForeignKey("arbeitseinsaetze.id", ondelete="CASCADE"), nullable=False),
        sa.Column("mitglied_id", sa.String(36),
                  sa.ForeignKey("mitglieder.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.Enum("ANGEMELDET", "ERSCHIENEN", "NICHT_ERSCHIENEN",
                                     name="teilnahmestatus"), nullable=False),
        sa.Column("stunden_geleistet", sa.Numeric(4, 1), nullable=True),
        sa.Column("notiz", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("einsatz_id", "mitglied_id", name="uq_einsatz_mitglied"),
    )
    op.create_index("ix_einsatz_teilnahmen_einsatz_id", "einsatz_teilnahmen", ["einsatz_id"])
    op.create_index("ix_einsatz_teilnahmen_mitglied_id", "einsatz_teilnahmen", ["mitglied_id"])


def downgrade() -> None:
    op.drop_table("einsatz_teilnahmen")
    op.drop_table("arbeitseinsaetze")
    op.drop_table("patenschaften")
    op.drop_table("mitglied_vereinsrolle")
    op.drop_table("vereinsrollen")
    op.drop_table("pflichtstunden_konfiguration")
    sa.Enum(name="pflichtstundenmodus").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="befreiungsgrund").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="einsatztyp").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="teilnahmestatus").drop(op.get_bind(), checkfirst=True)
