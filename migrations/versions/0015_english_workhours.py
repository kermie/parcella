"""Pflichtstunden-Modul auf Englisch umstellen (WorkSession/ClubRole/Sponsorship)

Revision ID: 0015_english_workhours
Revises: 0014_english_core
Create Date: 2026-07-14
"""
from typing import Union
from alembic import op
import sqlalchemy as sa

revision: str = "0015_english_workhours"
down_revision: Union[str, None] = "0014_english_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -----------------------------------------------------------------
    # 1. Enum-Typen umbenennen + Werte aktualisieren
    # -----------------------------------------------------------------
    op.execute("ALTER TYPE pflichtstundenmodus RENAME TO workhoursmode_old")
    op.execute("CREATE TYPE workhoursmode AS ENUM ('PER_PARCEL', 'PER_MEMBER')")
    op.execute("ALTER TABLE pflichtstunden_konfiguration ALTER COLUMN modus DROP DEFAULT")
    op.execute("""
        ALTER TABLE pflichtstunden_konfiguration ALTER COLUMN modus TYPE workhoursmode USING (
            CASE modus::text
                WHEN 'pro_pachtvertrag' THEN 'PER_PARCEL'
                WHEN 'pro_mitglied' THEN 'PER_MEMBER'
                ELSE 'PER_PARCEL'
            END
        )::workhoursmode
    """)
    op.execute("ALTER TABLE pflichtstunden_konfiguration ALTER COLUMN modus SET DEFAULT 'PER_PARCEL'::workhoursmode")
    op.execute("DROP TYPE workhoursmode_old")

    op.execute("ALTER TYPE befreiungsgrund RENAME TO exemptionreason_old")
    op.execute("CREATE TYPE exemptionreason AS ENUM ('BOARD', 'EXTENDED_BOARD', 'ILLNESS', 'AGE', 'OTHER')")
    op.execute("""
        ALTER TABLE vereinsrollen ALTER COLUMN befreiungsgrund TYPE exemptionreason USING (
            CASE befreiungsgrund::text
                WHEN 'VORSTAND' THEN 'BOARD'
                WHEN 'ERWEITERTER_VORSTAND' THEN 'EXTENDED_BOARD'
                WHEN 'KRANKHEIT' THEN 'ILLNESS'
                WHEN 'ALTER' THEN 'AGE'
                WHEN 'SONSTIG' THEN 'OTHER'
                ELSE NULL
            END
        )::exemptionreason
    """)
    op.execute("DROP TYPE exemptionreason_old")

    op.execute("ALTER TYPE einsatztyp RENAME TO sessiontype_old")
    op.execute("CREATE TYPE sessiontype AS ENUM ('STANDARD', 'SPECIAL')")
    op.execute("ALTER TABLE arbeitseinsaetze ALTER COLUMN typ DROP DEFAULT")
    op.execute("""
        ALTER TABLE arbeitseinsaetze ALTER COLUMN typ TYPE sessiontype USING (
            CASE typ::text
                WHEN 'STANDARD' THEN 'STANDARD'
                WHEN 'BESONDERS' THEN 'SPECIAL'
                ELSE 'STANDARD'
            END
        )::sessiontype
    """)
    op.execute("ALTER TABLE arbeitseinsaetze ALTER COLUMN typ SET DEFAULT 'STANDARD'::sessiontype")
    op.execute("DROP TYPE sessiontype_old")

    op.execute("ALTER TYPE teilnahmestatus RENAME TO participationstatus_old")
    op.execute("CREATE TYPE participationstatus AS ENUM ('REGISTERED', 'ATTENDED', 'NO_SHOW')")
    op.execute("ALTER TABLE einsatz_teilnahmen ALTER COLUMN status DROP DEFAULT")
    op.execute("""
        ALTER TABLE einsatz_teilnahmen ALTER COLUMN status TYPE participationstatus USING (
            CASE status::text
                WHEN 'ANGEMELDET' THEN 'REGISTERED'
                WHEN 'ERSCHIENEN' THEN 'ATTENDED'
                WHEN 'NICHT_ERSCHIENEN' THEN 'NO_SHOW'
                ELSE 'REGISTERED'
            END
        )::participationstatus
    """)
    op.execute("ALTER TABLE einsatz_teilnahmen ALTER COLUMN status SET DEFAULT 'REGISTERED'::participationstatus")
    op.execute("DROP TYPE participationstatus_old")

    # -----------------------------------------------------------------
    # 2. Tabellen umbenennen
    # -----------------------------------------------------------------
    op.rename_table("pflichtstunden_konfiguration", "work_hours_configuration")
    op.rename_table("vereinsrollen", "club_roles")
    op.rename_table("mitglied_vereinsrolle", "member_club_roles")
    op.rename_table("patenschaften", "sponsorships")
    op.rename_table("arbeitseinsaetze", "work_sessions")
    op.rename_table("einsatz_teilnahmen", "session_participations")

    # -----------------------------------------------------------------
    # 3. Spalten in work_hours_configuration umbenennen
    # -----------------------------------------------------------------
    op.alter_column("work_hours_configuration", "jahr", new_column_name="year")
    op.alter_column("work_hours_configuration", "stunden_gesamt", new_column_name="hours_required")
    op.alter_column("work_hours_configuration", "stundensatz_eur", new_column_name="rate_per_hour_eur")
    op.alter_column("work_hours_configuration", "modus", new_column_name="mode")
    op.alter_column("work_hours_configuration", "notiz", new_column_name="note")

    # -----------------------------------------------------------------
    # 4. Spalten in club_roles umbenennen
    # -----------------------------------------------------------------
    op.alter_column("club_roles", "beschreibung", new_column_name="description")
    op.alter_column("club_roles", "pflichtstunden_befreit", new_column_name="hours_exempt")
    op.alter_column("club_roles", "befreiungsgrund", new_column_name="exemption_reason")

    # -----------------------------------------------------------------
    # 5. Spalten in member_club_roles umbenennen
    # -----------------------------------------------------------------
    op.alter_column("member_club_roles", "mitglied_id", new_column_name="member_id")
    op.alter_column("member_club_roles", "vereinsrolle_id", new_column_name="club_role_id")
    op.alter_column("member_club_roles", "jahr", new_column_name="year")
    op.alter_column("member_club_roles", "von", new_column_name="valid_from")
    op.alter_column("member_club_roles", "bis", new_column_name="valid_until")
    op.alter_column("member_club_roles", "notiz", new_column_name="note")
    op.execute("ALTER TABLE member_club_roles DROP CONSTRAINT IF EXISTS uq_mitglied_vereinsrolle_jahr")
    op.create_unique_constraint(
        "uq_member_club_role_year", "member_club_roles", ["member_id", "club_role_id", "year"]
    )

    # -----------------------------------------------------------------
    # 6. Spalten in sponsorships umbenennen
    # -----------------------------------------------------------------
    op.alter_column("sponsorships", "mitglied_id", new_column_name="member_id")
    op.alter_column("sponsorships", "bereich", new_column_name="area")
    op.alter_column("sponsorships", "beschreibung", new_column_name="description")
    op.alter_column("sponsorships", "stunden_anrechenbar", new_column_name="credited_hours")
    op.alter_column("sponsorships", "von", new_column_name="valid_from")
    op.alter_column("sponsorships", "bis", new_column_name="valid_until")

    # -----------------------------------------------------------------
    # 7. Spalten in work_sessions umbenennen
    # -----------------------------------------------------------------
    op.alter_column("work_sessions", "titel", new_column_name="title")
    op.alter_column("work_sessions", "beschreibung", new_column_name="description")
    op.alter_column("work_sessions", "typ", new_column_name="type")
    op.alter_column("work_sessions", "datum", new_column_name="date")
    op.alter_column("work_sessions", "uhrzeit_von", new_column_name="time_from")
    op.alter_column("work_sessions", "uhrzeit_bis", new_column_name="time_until")
    op.alter_column("work_sessions", "max_teilnehmer", new_column_name="max_participants")
    op.alter_column("work_sessions", "stunden_pro_teilnehmer", new_column_name="hours_per_participant")
    op.alter_column("work_sessions", "erstellt_von_id", new_column_name="created_by_id")

    # -----------------------------------------------------------------
    # 8. Spalten in session_participations umbenennen
    # -----------------------------------------------------------------
    op.alter_column("session_participations", "einsatz_id", new_column_name="session_id")
    op.alter_column("session_participations", "mitglied_id", new_column_name="member_id")
    op.alter_column("session_participations", "stunden_geleistet", new_column_name="hours_completed")
    op.alter_column("session_participations", "notiz", new_column_name="note")
    op.execute("ALTER TABLE session_participations DROP CONSTRAINT IF EXISTS uq_einsatz_mitglied")
    op.create_unique_constraint(
        "uq_session_member", "session_participations", ["session_id", "member_id"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_session_member", "session_participations", type_="unique")
    op.create_unique_constraint("uq_einsatz_mitglied", "session_participations", ["einsatz_id", "mitglied_id"])
    op.alter_column("session_participations", "note", new_column_name="notiz")
    op.alter_column("session_participations", "hours_completed", new_column_name="stunden_geleistet")
    op.alter_column("session_participations", "member_id", new_column_name="mitglied_id")
    op.alter_column("session_participations", "session_id", new_column_name="einsatz_id")

    op.alter_column("work_sessions", "created_by_id", new_column_name="erstellt_von_id")
    op.alter_column("work_sessions", "hours_per_participant", new_column_name="stunden_pro_teilnehmer")
    op.alter_column("work_sessions", "max_participants", new_column_name="max_teilnehmer")
    op.alter_column("work_sessions", "time_until", new_column_name="uhrzeit_bis")
    op.alter_column("work_sessions", "time_from", new_column_name="uhrzeit_von")
    op.alter_column("work_sessions", "date", new_column_name="datum")
    op.alter_column("work_sessions", "type", new_column_name="typ")
    op.alter_column("work_sessions", "description", new_column_name="beschreibung")
    op.alter_column("work_sessions", "title", new_column_name="titel")

    op.alter_column("sponsorships", "valid_until", new_column_name="bis")
    op.alter_column("sponsorships", "valid_from", new_column_name="von")
    op.alter_column("sponsorships", "credited_hours", new_column_name="stunden_anrechenbar")
    op.alter_column("sponsorships", "description", new_column_name="beschreibung")
    op.alter_column("sponsorships", "area", new_column_name="bereich")
    op.alter_column("sponsorships", "member_id", new_column_name="mitglied_id")

    op.drop_constraint("uq_member_club_role_year", "member_club_roles", type_="unique")
    op.create_unique_constraint(
        "uq_mitglied_vereinsrolle_jahr", "member_club_roles", ["mitglied_id", "vereinsrolle_id", "jahr"]
    )
    op.alter_column("member_club_roles", "note", new_column_name="notiz")
    op.alter_column("member_club_roles", "valid_until", new_column_name="bis")
    op.alter_column("member_club_roles", "valid_from", new_column_name="von")
    op.alter_column("member_club_roles", "year", new_column_name="jahr")
    op.alter_column("member_club_roles", "club_role_id", new_column_name="vereinsrolle_id")
    op.alter_column("member_club_roles", "member_id", new_column_name="mitglied_id")

    op.alter_column("club_roles", "exemption_reason", new_column_name="befreiungsgrund")
    op.alter_column("club_roles", "hours_exempt", new_column_name="pflichtstunden_befreit")
    op.alter_column("club_roles", "description", new_column_name="beschreibung")

    op.alter_column("work_hours_configuration", "note", new_column_name="notiz")
    op.alter_column("work_hours_configuration", "mode", new_column_name="modus")
    op.alter_column("work_hours_configuration", "rate_per_hour_eur", new_column_name="stundensatz_eur")
    op.alter_column("work_hours_configuration", "hours_required", new_column_name="stunden_gesamt")
    op.alter_column("work_hours_configuration", "year", new_column_name="jahr")

    op.rename_table("session_participations", "einsatz_teilnahmen")
    op.rename_table("work_sessions", "arbeitseinsaetze")
    op.rename_table("sponsorships", "patenschaften")
    op.rename_table("member_club_roles", "mitglied_vereinsrolle")
    op.rename_table("club_roles", "vereinsrollen")
    op.rename_table("work_hours_configuration", "pflichtstunden_konfiguration")

    op.execute("ALTER TYPE participationstatus RENAME TO participationstatus_old")
    op.execute("CREATE TYPE teilnahmestatus AS ENUM ('ANGEMELDET', 'ERSCHIENEN', 'NICHT_ERSCHIENEN')")
    op.execute("ALTER TABLE einsatz_teilnahmen ALTER COLUMN status DROP DEFAULT")
    op.execute("""
        ALTER TABLE einsatz_teilnahmen ALTER COLUMN status TYPE teilnahmestatus USING (
            CASE status::text
                WHEN 'REGISTERED' THEN 'ANGEMELDET'
                WHEN 'ATTENDED' THEN 'ERSCHIENEN'
                WHEN 'NO_SHOW' THEN 'NICHT_ERSCHIENEN'
                ELSE 'ANGEMELDET'
            END
        )::teilnahmestatus
    """)
    op.execute("ALTER TABLE einsatz_teilnahmen ALTER COLUMN status SET DEFAULT 'ANGEMELDET'::teilnahmestatus")
    op.execute("DROP TYPE participationstatus_old")

    op.execute("ALTER TYPE sessiontype RENAME TO sessiontype_old")
    op.execute("CREATE TYPE einsatztyp AS ENUM ('STANDARD', 'BESONDERS')")
    op.execute("ALTER TABLE arbeitseinsaetze ALTER COLUMN typ DROP DEFAULT")
    op.execute("""
        ALTER TABLE arbeitseinsaetze ALTER COLUMN typ TYPE einsatztyp USING (
            CASE typ::text
                WHEN 'STANDARD' THEN 'STANDARD'
                WHEN 'SPECIAL' THEN 'BESONDERS'
                ELSE 'STANDARD'
            END
        )::einsatztyp
    """)
    op.execute("ALTER TABLE arbeitseinsaetze ALTER COLUMN typ SET DEFAULT 'STANDARD'::einsatztyp")
    op.execute("DROP TYPE sessiontype_old")

    op.execute("ALTER TYPE exemptionreason RENAME TO exemptionreason_old")
    op.execute("CREATE TYPE befreiungsgrund AS ENUM ('VORSTAND', 'ERWEITERTER_VORSTAND', 'KRANKHEIT', 'ALTER', 'SONSTIG')")
    op.execute("""
        ALTER TABLE vereinsrollen ALTER COLUMN befreiungsgrund TYPE befreiungsgrund USING (
            CASE befreiungsgrund::text
                WHEN 'BOARD' THEN 'VORSTAND'
                WHEN 'EXTENDED_BOARD' THEN 'ERWEITERTER_VORSTAND'
                WHEN 'ILLNESS' THEN 'KRANKHEIT'
                WHEN 'AGE' THEN 'ALTER'
                WHEN 'OTHER' THEN 'SONSTIG'
                ELSE NULL
            END
        )::befreiungsgrund
    """)
    op.execute("DROP TYPE exemptionreason_old")

    op.execute("ALTER TYPE workhoursmode RENAME TO workhoursmode_old")
    op.execute("CREATE TYPE pflichtstundenmodus AS ENUM ('pro_pachtvertrag', 'pro_mitglied')")
    op.execute("ALTER TABLE pflichtstunden_konfiguration ALTER COLUMN modus DROP DEFAULT")
    op.execute("""
        ALTER TABLE pflichtstunden_konfiguration ALTER COLUMN modus TYPE pflichtstundenmodus USING (
            CASE modus::text
                WHEN 'PER_PARCEL' THEN 'pro_pachtvertrag'
                WHEN 'PER_MEMBER' THEN 'pro_mitglied'
                ELSE 'pro_pachtvertrag'
            END
        )::pflichtstundenmodus
    """)
    op.execute("ALTER TABLE pflichtstunden_konfiguration ALTER COLUMN modus SET DEFAULT 'pro_pachtvertrag'::pflichtstundenmodus")
    op.execute("DROP TYPE workhoursmode_old")
