"""Initial schema (baseline)

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-01 00:00:00

This migration reflects the schema that already exists in existing
installations via Base.metadata.create_all(). It is marked as already
applied with `alembic stamp head`, WITHOUT recreating the tables.

For new installations (empty database), `alembic upgrade head` actually
runs the CREATE statements below.

Note: the table/column names and enum values below (e.g. "benutzer",
"rolle", "VORSTAND") are historical and were later renamed to English
by subsequent migrations (see docs/architecture-decisions.md, "module
to English" entries) -- left as-is here since editing an already-applied
migration's SQL would break `alembic stamp head` and fresh installs
replaying this history.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.create_table(
        "benutzer",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("passwort_hash", sa.String(255), nullable=True),
        sa.Column("rolle", sa.Enum("ADMIN", "VORSTAND", "KASSIERER", "LESEND", name="benutzerrolle"), nullable=False),
        sa.Column("ist_aktiv", sa.Boolean(), nullable=False),
        sa.Column("letzter_login", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_benutzer_email", "benutzer", ["email"])

    op.create_table(
        "einladungen",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("token", sa.String(255), nullable=False, unique=True),
        sa.Column("rolle", sa.Enum("ADMIN", "VORSTAND", "KASSIERER", "LESEND", name="benutzerrolle"), nullable=False),
        sa.Column("status", sa.Enum("AUSSTEHEND", "ANGENOMMEN", "ABGELAUFEN", name="einladungstatus"), nullable=False),
        sa.Column("eingeladen_von_id", sa.String(36), sa.ForeignKey("benutzer.id", ondelete="SET NULL"), nullable=True),
        sa.Column("gueltig_bis", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_einladungen_email", "einladungen", ["email"])

    op.create_table(
        "mitglieder",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("vorname", sa.String(100), nullable=False),
        sa.Column("nachname", sa.String(100), nullable=False),
        sa.Column("geburtsdatum", sa.Date(), nullable=True),
        sa.Column("strasse", sa.String(255), nullable=True),
        sa.Column("plz", sa.String(10), nullable=True),
        sa.Column("ort", sa.String(100), nullable=True),
        sa.Column("iban", sa.String(34), nullable=True),
        sa.Column("mitglied_seit", sa.Date(), nullable=True),
        sa.Column("mitglied_bis", sa.Date(), nullable=True),
        sa.Column("email_benachrichtigungen", sa.Boolean(), nullable=False),
        sa.Column("notizen", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "mitglied_telefon",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("mitglied_id", sa.String(36), sa.ForeignKey("mitglieder.id", ondelete="CASCADE"), nullable=False),
        sa.Column("nummer", sa.String(50), nullable=False),
        sa.Column("bezeichnung", sa.String(50), nullable=True),
        sa.Column("ist_primaer", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_mitglied_telefon_mitglied_id", "mitglied_telefon", ["mitglied_id"])

    op.create_table(
        "mitglied_email",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("mitglied_id", sa.String(36), sa.ForeignKey("mitglieder.id", ondelete="CASCADE"), nullable=False),
        sa.Column("adresse", sa.String(255), nullable=False),
        sa.Column("bezeichnung", sa.String(50), nullable=True),
        sa.Column("ist_primaer", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_mitglied_email_mitglied_id", "mitglied_email", ["mitglied_id"])

    op.create_table(
        "parzellen",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("gartennummer", sa.String(20), nullable=False, unique=True),
        sa.Column("flaeche_qm", sa.Numeric(10, 2), nullable=True),
        sa.Column("status", sa.Enum("AKTIV", "GEKUENDIGT", "GELOESCHT", name="parzellestatus"), nullable=False),
        sa.Column("kuendigung_datum", sa.Date(), nullable=True),
        sa.Column("kuendigung_notiz", sa.Text(), nullable=True),
        sa.Column("notizen", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_parzellen_gartennummer", "parzellen", ["gartennummer"])

    op.create_table(
        "mitglied_parzelle",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("mitglied_id", sa.String(36), sa.ForeignKey("mitglieder.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parzelle_id", sa.String(36), sa.ForeignKey("parzellen.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ist_hauptpaechter", sa.Boolean(), nullable=False),
        sa.Column("zuordnung_von", sa.Date(), nullable=True),
        sa.Column("zuordnung_bis", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("mitglied_id", "parzelle_id", name="uq_mitglied_parzelle"),
    )
    op.create_index("ix_mitglied_parzelle_mitglied_id", "mitglied_parzelle", ["mitglied_id"])
    op.create_index("ix_mitglied_parzelle_parzelle_id", "mitglied_parzelle", ["parzelle_id"])

    op.create_table(
        "vereinseinstellungen",
        sa.Column("schluessel", sa.String(100), primary_key=True),
        sa.Column("wert", sa.Text(), nullable=True),
        sa.Column("beschreibung", sa.String(255), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("vereinseinstellungen")
    op.drop_table("mitglied_parzelle")
    op.drop_table("parzellen")
    op.drop_table("mitglied_email")
    op.drop_table("mitglied_telefon")
    op.drop_table("mitglieder")
    op.drop_table("einladungen")
    op.drop_table("benutzer")
    sa.Enum(name="benutzerrolle").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="einladungstatus").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="parzellestatus").drop(op.get_bind(), checkfirst=True)
