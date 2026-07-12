"""Kernmodul auf Englisch umstellen: mitglieder->members, parzellen->parcels

Grosser, aber notwendiger Schritt: der jetzige Zeitpunkt (noch keine
produktiven Fremdnutzer) ist der guenstigste, den es je geben wird, um
das Datenbankschema auf durchgaengig englische Bezeichner umzustellen.

Revision ID: 0014_english_core
Revises: 0013_einkaufswuensche
Create Date: 2026-07-13
"""
from typing import Union
from alembic import op
import sqlalchemy as sa

revision: str = "0014_english_core"
down_revision: Union[str, None] = "0013_einkaufswuensche"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---------------------------------------------------------------
    # 1. Enum-Typ fuer Parcel-Status umbenennen + Werte aktualisieren
    # ---------------------------------------------------------------
    op.execute("ALTER TYPE parzellestatus RENAME TO parcelstatus_old")
    op.execute("CREATE TYPE parcelstatus AS ENUM ('ACTIVE', 'TERMINATED', 'DELETED')")
    op.execute("ALTER TABLE parzellen ALTER COLUMN status DROP DEFAULT")
    op.execute("""
        ALTER TABLE parzellen ALTER COLUMN status TYPE parcelstatus USING (
            CASE status::text
                WHEN 'AKTIV' THEN 'ACTIVE'
                WHEN 'GEKUENDIGT' THEN 'TERMINATED'
                WHEN 'GELOESCHT' THEN 'DELETED'
            END
        )::parcelstatus
    """)
    op.execute("ALTER TABLE parzellen ALTER COLUMN status SET DEFAULT 'ACTIVE'::parcelstatus")
    op.execute("DROP TYPE parcelstatus_old")

    # ---------------------------------------------------------------
    # 2. Tabellen umbenennen
    # ---------------------------------------------------------------
    op.rename_table("mitglieder", "members")
    op.rename_table("mitglied_telefon", "member_phones")
    op.rename_table("mitglied_email", "member_emails")
    op.rename_table("parzellen", "parcels")
    op.rename_table("mitglied_parzelle", "member_parcels")

    # ---------------------------------------------------------------
    # 3. Spalten in members umbenennen
    # ---------------------------------------------------------------
    op.alter_column("members", "vorname", new_column_name="first_name")
    op.alter_column("members", "nachname", new_column_name="last_name")
    op.alter_column("members", "geburtsdatum", new_column_name="date_of_birth")
    op.alter_column("members", "strasse", new_column_name="street")
    op.alter_column("members", "plz", new_column_name="postal_code")
    op.alter_column("members", "ort", new_column_name="city")
    op.alter_column("members", "mitglied_seit", new_column_name="member_since")
    op.alter_column("members", "mitglied_bis", new_column_name="member_until")
    op.alter_column("members", "email_benachrichtigungen", new_column_name="email_notifications")
    op.alter_column("members", "notizen", new_column_name="notes")

    # ---------------------------------------------------------------
    # 4. Spalten in member_phones / member_emails umbenennen
    # ---------------------------------------------------------------
    op.alter_column("member_phones", "mitglied_id", new_column_name="member_id")
    op.alter_column("member_phones", "nummer", new_column_name="number")
    op.alter_column("member_phones", "bezeichnung", new_column_name="label")
    op.alter_column("member_phones", "ist_primaer", new_column_name="is_primary")

    op.alter_column("member_emails", "mitglied_id", new_column_name="member_id")
    op.alter_column("member_emails", "adresse", new_column_name="address")
    op.alter_column("member_emails", "bezeichnung", new_column_name="label")
    op.alter_column("member_emails", "ist_primaer", new_column_name="is_primary")

    # ---------------------------------------------------------------
    # 5. Spalten in parcels umbenennen
    # ---------------------------------------------------------------
    op.alter_column("parcels", "gartennummer", new_column_name="plot_number")
    op.alter_column("parcels", "flaeche_qm", new_column_name="area_sqm")
    op.alter_column("parcels", "kuendigung_notiz", new_column_name="termination_note")
    op.alter_column("parcels", "notizen", new_column_name="notes")

    # ---------------------------------------------------------------
    # 6. Spalten in member_parcels umbenennen
    # ---------------------------------------------------------------
    op.alter_column("member_parcels", "mitglied_id", new_column_name="member_id")
    op.alter_column("member_parcels", "parzelle_id", new_column_name="parcel_id")
    op.alter_column("member_parcels", "ist_hauptpaechter", new_column_name="is_primary_tenant")
    op.alter_column("member_parcels", "zuordnung_von", new_column_name="assigned_from")
    op.alter_column("member_parcels", "zuordnung_bis", new_column_name="assigned_until")

    # Unique-Constraint-Name aktualisieren (alter Name bezog sich auf alte Spalten)
    op.execute("ALTER TABLE member_parcels DROP CONSTRAINT IF EXISTS uq_mitglied_parzelle")
    op.create_unique_constraint("uq_member_parcel", "member_parcels", ["member_id", "parcel_id"])

    # ---------------------------------------------------------------
    # 7. Fremdschluessel in ANDEREN Modulen zeigen jetzt auf neue
    #    Tabellennamen - PostgreSQL aktualisiert die Constraint-Ziele
    #    automatisch beim Umbenennen der Zieltabelle (kein Handeln noetig),
    #    ABER die FK-Spalten in diesen Modulen (z.B. Zaehlpunkt.parzelle_id,
    #    ParzelleVersicherung.parzelle_id) behalten bewusst ihre bisherigen
    #    Namen - deren Umbenennung folgt in den jeweiligen Modul-Runden.
    # ---------------------------------------------------------------


def downgrade() -> None:
    op.drop_constraint("uq_member_parcel", "member_parcels", type_="unique")
    op.create_unique_constraint("uq_mitglied_parzelle", "member_parcels", ["member_id", "parcel_id"])

    op.alter_column("member_parcels", "assigned_until", new_column_name="zuordnung_bis")
    op.alter_column("member_parcels", "assigned_from", new_column_name="zuordnung_von")
    op.alter_column("member_parcels", "is_primary_tenant", new_column_name="ist_hauptpaechter")
    op.alter_column("member_parcels", "parcel_id", new_column_name="parzelle_id")
    op.alter_column("member_parcels", "member_id", new_column_name="mitglied_id")

    op.alter_column("parcels", "notes", new_column_name="notizen")
    op.alter_column("parcels", "termination_note", new_column_name="kuendigung_notiz")
    op.alter_column("parcels", "area_sqm", new_column_name="flaeche_qm")
    op.alter_column("parcels", "plot_number", new_column_name="gartennummer")

    op.alter_column("member_emails", "is_primary", new_column_name="ist_primaer")
    op.alter_column("member_emails", "label", new_column_name="bezeichnung")
    op.alter_column("member_emails", "address", new_column_name="adresse")
    op.alter_column("member_emails", "member_id", new_column_name="mitglied_id")

    op.alter_column("member_phones", "is_primary", new_column_name="ist_primaer")
    op.alter_column("member_phones", "label", new_column_name="bezeichnung")
    op.alter_column("member_phones", "number", new_column_name="nummer")
    op.alter_column("member_phones", "member_id", new_column_name="mitglied_id")

    op.alter_column("members", "notes", new_column_name="notizen")
    op.alter_column("members", "email_notifications", new_column_name="email_benachrichtigungen")
    op.alter_column("members", "member_until", new_column_name="mitglied_bis")
    op.alter_column("members", "member_since", new_column_name="mitglied_seit")
    op.alter_column("members", "city", new_column_name="ort")
    op.alter_column("members", "postal_code", new_column_name="plz")
    op.alter_column("members", "street", new_column_name="strasse")
    op.alter_column("members", "date_of_birth", new_column_name="geburtsdatum")
    op.alter_column("members", "last_name", new_column_name="nachname")
    op.alter_column("members", "first_name", new_column_name="vorname")

    op.rename_table("member_parcels", "mitglied_parzelle")
    op.rename_table("parcels", "parzellen")
    op.rename_table("member_emails", "mitglied_email")
    op.rename_table("member_phones", "mitglied_telefon")
    op.rename_table("members", "mitglieder")

    op.execute("ALTER TYPE parcelstatus RENAME TO parcelstatus_old")
    op.execute("CREATE TYPE parzellestatus AS ENUM ('AKTIV', 'GEKUENDIGT', 'GELOESCHT')")
    op.execute("ALTER TABLE parzellen ALTER COLUMN status DROP DEFAULT")
    op.execute("""
        ALTER TABLE parzellen ALTER COLUMN status TYPE parzellestatus USING (
            CASE status::text
                WHEN 'ACTIVE' THEN 'AKTIV'
                WHEN 'TERMINATED' THEN 'GEKUENDIGT'
                WHEN 'DELETED' THEN 'GELOESCHT'
            END
        )::parzellestatus
    """)
    op.execute("ALTER TABLE parzellen ALTER COLUMN status SET DEFAULT 'AKTIV'::parzellestatus")
    op.execute("DROP TYPE parcelstatus_old")
