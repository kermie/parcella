"""Zaehlerwesen generalisieren: Wasser + Strom ueber gemeinsames Schema

Benennt die bisherigen wasser-spezifischen Tabellen in generische Namen um
und fuegt ein "medium"-Feld hinzu (WASSER/STROM), damit Wasser- und
Stromzaehler dieselbe Datenstruktur und Codebasis nutzen koennen.

Revision ID: 0008_zaehlerwesen
Revises: 0007_wasser_modul
Create Date: 2026-07-07
"""
from typing import Union
from alembic import op
import sqlalchemy as sa

revision: str = "0008_zaehlerwesen"
down_revision: Union[str, None] = "0007_wasser_modul"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Enum-Typ fuer "typ" umbenennen (wasseranschlusstyp -> zaehlpunkttyp)
    op.execute("ALTER TYPE wasseranschlusstyp RENAME TO zaehlpunkttyp")

    # 2. Neuen Enum-Typ fuer "medium" anlegen
    op.execute("CREATE TYPE zaehlermedium AS ENUM ('WASSER', 'STROM')")

    # 3. Tabelle wasseranschluesse -> zaehlpunkte umbenennen
    op.rename_table("wasseranschluesse", "zaehlpunkte")

    # 4. Neue Spalte "medium" hinzufuegen, bestehende Zeilen sind alle Wasser
    op.add_column(
        "zaehlpunkte",
        sa.Column("medium", sa.Enum("WASSER", "STROM", name="zaehlermedium"),
                  nullable=False, server_default="WASSER"),
    )

    # 5. Tabelle wasseruhren -> zaehler umbenennen, Spalte anschluss_id -> zaehlpunkt_id
    op.rename_table("wasseruhren", "zaehler")
    op.alter_column("zaehler", "anschluss_id", new_column_name="zaehlpunkt_id")
    # Anfangsstand-Praezision erhoehen (Numeric(10,1) -> Numeric(12,1)) fuer
    # hoehere Stromzaehlerstaende
    op.alter_column("zaehler", "anfangsstand", type_=sa.Numeric(12, 1))

    # 6. Zaehlerstaende: Spalte wasseruhr_id -> zaehler_id, Constraint umbenennen
    op.drop_constraint("uq_wasseruhr_jahr", "zaehlerstaende", type_="unique")
    op.alter_column("zaehlerstaende", "wasseruhr_id", new_column_name="zaehler_id")
    op.alter_column("zaehlerstaende", "stand", type_=sa.Numeric(12, 1))
    op.create_unique_constraint("uq_zaehler_jahr", "zaehlerstaende", ["zaehler_id", "jahr"])


def downgrade() -> None:
    op.drop_constraint("uq_zaehler_jahr", "zaehlerstaende", type_="unique")
    op.alter_column("zaehlerstaende", "zaehler_id", new_column_name="wasseruhr_id")
    op.alter_column("zaehlerstaende", "stand", type_=sa.Numeric(10, 1))
    op.create_unique_constraint("uq_wasseruhr_jahr", "zaehlerstaende", ["wasseruhr_id", "jahr"])

    op.alter_column("zaehler", "anfangsstand", type_=sa.Numeric(10, 1))
    op.alter_column("zaehler", "zaehlpunkt_id", new_column_name="anschluss_id")
    op.rename_table("zaehler", "wasseruhren")

    op.drop_column("zaehlpunkte", "medium")
    op.rename_table("zaehlpunkte", "wasseranschluesse")

    op.execute("DROP TYPE zaehlermedium")
    op.execute("ALTER TYPE zaehlpunkttyp RENAME TO wasseranschlusstyp")
