"""Befreiungsgrund: ERWEITERTER_VORSTAND hinzufügen

Revision ID: 0003_befreiungsgrund_erweiterter_vorstand
Revises: 0002_pflichtstunden
Create Date: 2026-07-03
"""
from typing import Union
from alembic import op

revision: str = "0003_erw_vorstand"
down_revision: Union[str, None] = "0002_pflichtstunden"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE befreiungsgrund ADD VALUE IF NOT EXISTS 'erweiterter_vorstand'")


def downgrade() -> None:
    # PostgreSQL erlaubt kein direktes Entfernen von Enum-Werten.
    # Downgrade ist hier ein No-Op – der Wert bleibt im Enum erhalten,
    # wird aber vom Code nicht mehr verwendet.
    pass
