"""Zaehlerwesen-Modul auf Englisch umstellen (MeteringPoint/Meter/MeterReading)

Revision ID: 0016_english_metering
Revises: 0015_english_workhours
Create Date: 2026-07-15
"""
from typing import Union
from alembic import op
import sqlalchemy as sa

revision: str = "0016_english_metering"
down_revision: Union[str, None] = "0015_english_workhours"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -----------------------------------------------------------------
    # 1. Enum-Typen umbenennen + Werte aktualisieren
    # -----------------------------------------------------------------
    op.execute("ALTER TYPE zaehlermedium RENAME TO meteringmedium_old")
    op.execute("CREATE TYPE meteringmedium AS ENUM ('WATER', 'ELECTRICITY')")
    op.execute("ALTER TABLE zaehlpunkte ALTER COLUMN medium DROP DEFAULT")
    op.execute("""
        ALTER TABLE zaehlpunkte ALTER COLUMN medium TYPE meteringmedium USING (
            CASE medium::text
                WHEN 'WASSER' THEN 'WATER'
                WHEN 'STROM' THEN 'ELECTRICITY'
                ELSE 'WATER'
            END
        )::meteringmedium
    """)
    op.execute("DROP TYPE meteringmedium_old")

    op.execute("ALTER TYPE zaehlpunkttyp RENAME TO meteringpointtype_old")
    op.execute("CREATE TYPE meteringpointtype AS ENUM ('MAIN_METER', 'PARCEL', 'CLUB')")
    op.execute("ALTER TABLE zaehlpunkte ALTER COLUMN typ DROP DEFAULT")
    op.execute("""
        ALTER TABLE zaehlpunkte ALTER COLUMN typ TYPE meteringpointtype USING (
            CASE typ::text
                WHEN 'HAUPTZAEHLER' THEN 'MAIN_METER'
                WHEN 'PARZELLE' THEN 'PARCEL'
                WHEN 'VEREIN' THEN 'CLUB'
                ELSE 'PARCEL'
            END
        )::meteringpointtype
    """)
    op.execute("DROP TYPE meteringpointtype_old")

    # -----------------------------------------------------------------
    # 2. Tabellen umbenennen
    # -----------------------------------------------------------------
    op.rename_table("zaehlpunkte", "metering_points")
    op.rename_table("zaehler", "meters")
    op.rename_table("zaehlerstaende", "meter_readings")

    # -----------------------------------------------------------------
    # 3. Spalten in metering_points umbenennen
    # -----------------------------------------------------------------
    op.alter_column("metering_points", "typ", new_column_name="type")
    op.alter_column("metering_points", "parzelle_id", new_column_name="parcel_id")
    op.alter_column("metering_points", "bezeichnung", new_column_name="label")
    op.alter_column("metering_points", "notizen", new_column_name="notes")

    # -----------------------------------------------------------------
    # 4. Spalten in meters umbenennen
    # -----------------------------------------------------------------
    op.alter_column("meters", "zaehlpunkt_id", new_column_name="metering_point_id")
    op.alter_column("meters", "nummer", new_column_name="number")
    op.alter_column("meters", "ist_aktiv", new_column_name="is_active")
    op.alter_column("meters", "geeicht_bis", new_column_name="calibrated_until")
    op.alter_column("meters", "eingebaut_am", new_column_name="installed_at")
    op.alter_column("meters", "ausgebaut_am", new_column_name="removed_at")
    op.alter_column("meters", "anfangsstand", new_column_name="initial_reading")
    op.alter_column("meters", "notizen", new_column_name="notes")

    # -----------------------------------------------------------------
    # 5. Spalten in meter_readings umbenennen
    # -----------------------------------------------------------------
    op.alter_column("meter_readings", "zaehler_id", new_column_name="meter_id")
    op.alter_column("meter_readings", "jahr", new_column_name="year")
    op.alter_column("meter_readings", "datum", new_column_name="date")
    op.alter_column("meter_readings", "stand", new_column_name="reading")
    op.alter_column("meter_readings", "erfasst_von_id", new_column_name="recorded_by_id")
    op.alter_column("meter_readings", "notiz", new_column_name="note")

    op.execute("ALTER TABLE meter_readings DROP CONSTRAINT IF EXISTS uq_zaehler_jahr")
    op.create_unique_constraint("uq_meter_year", "meter_readings", ["meter_id", "year"])


def downgrade() -> None:
    op.drop_constraint("uq_meter_year", "meter_readings", type_="unique")
    op.create_unique_constraint("uq_zaehler_jahr", "meter_readings", ["meter_id", "year"])

    op.alter_column("meter_readings", "note", new_column_name="notiz")
    op.alter_column("meter_readings", "recorded_by_id", new_column_name="erfasst_von_id")
    op.alter_column("meter_readings", "reading", new_column_name="stand")
    op.alter_column("meter_readings", "date", new_column_name="datum")
    op.alter_column("meter_readings", "year", new_column_name="jahr")
    op.alter_column("meter_readings", "meter_id", new_column_name="zaehler_id")

    op.alter_column("meters", "notes", new_column_name="notizen")
    op.alter_column("meters", "initial_reading", new_column_name="anfangsstand")
    op.alter_column("meters", "removed_at", new_column_name="ausgebaut_am")
    op.alter_column("meters", "installed_at", new_column_name="eingebaut_am")
    op.alter_column("meters", "calibrated_until", new_column_name="geeicht_bis")
    op.alter_column("meters", "is_active", new_column_name="ist_aktiv")
    op.alter_column("meters", "number", new_column_name="nummer")
    op.alter_column("meters", "metering_point_id", new_column_name="zaehlpunkt_id")

    op.alter_column("metering_points", "notes", new_column_name="notizen")
    op.alter_column("metering_points", "label", new_column_name="bezeichnung")
    op.alter_column("metering_points", "parcel_id", new_column_name="parzelle_id")
    op.alter_column("metering_points", "type", new_column_name="typ")

    op.rename_table("meter_readings", "zaehlerstaende")
    op.rename_table("meters", "zaehler")
    op.rename_table("metering_points", "zaehlpunkte")

    op.execute("ALTER TYPE meteringpointtype RENAME TO meteringpointtype_old")
    op.execute("CREATE TYPE zaehlpunkttyp AS ENUM ('HAUPTZAEHLER', 'PARZELLE', 'VEREIN')")
    op.execute("ALTER TABLE zaehlpunkte ALTER COLUMN typ DROP DEFAULT")
    op.execute("""
        ALTER TABLE zaehlpunkte ALTER COLUMN typ TYPE zaehlpunkttyp USING (
            CASE typ::text
                WHEN 'MAIN_METER' THEN 'HAUPTZAEHLER'
                WHEN 'PARCEL' THEN 'PARZELLE'
                WHEN 'CLUB' THEN 'VEREIN'
                ELSE 'PARZELLE'
            END
        )::zaehlpunkttyp
    """)
    op.execute("DROP TYPE meteringpointtype_old")

    op.execute("ALTER TYPE meteringmedium RENAME TO meteringmedium_old")
    op.execute("CREATE TYPE zaehlermedium AS ENUM ('WASSER', 'STROM')")
    op.execute("ALTER TABLE zaehlpunkte ALTER COLUMN medium DROP DEFAULT")
    op.execute("""
        ALTER TABLE zaehlpunkte ALTER COLUMN medium TYPE zaehlermedium USING (
            CASE medium::text
                WHEN 'WATER' THEN 'WASSER'
                WHEN 'ELECTRICITY' THEN 'STROM'
                ELSE 'WASSER'
            END
        )::zaehlermedium
    """)
    op.execute("DROP TYPE meteringmedium_old")
