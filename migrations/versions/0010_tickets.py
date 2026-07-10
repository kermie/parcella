"""Ticketsystem: Tickets und Nachrichten (Etappe 1)

Revision ID: 0010_tickets
Revises: 0009_versicherungen
Create Date: 2026-07-09
"""
from typing import Union
from alembic import op
import sqlalchemy as sa

revision: str = "0010_tickets"
down_revision: Union[str, None] = "0009_versicherungen"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tickets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("betreff", sa.String(255), nullable=False),
        sa.Column("status", sa.Enum(
            "NICHT_ZUGEWIESEN", "ZUGEWIESEN", "ZURUECKGESTELLT", "GESCHLOSSEN",
            name="ticketstatus"
        ), nullable=False),
        sa.Column("zugewiesen_an_id", sa.String(36),
                  sa.ForeignKey("benutzer.id", ondelete="SET NULL"), nullable=True),
        sa.Column("zurueckgestellt_bis", sa.Date(), nullable=True),
        sa.Column("mitglied_id", sa.String(36),
                  sa.ForeignKey("mitglieder.id", ondelete="SET NULL"), nullable=True),
        sa.Column("absender_email", sa.String(255), nullable=False),
        sa.Column("absender_name", sa.String(255), nullable=True),
        sa.Column("spam_verdacht", sa.Boolean(), nullable=False),
        sa.Column("spam_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("erstellt_am", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("aktualisiert_am", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("geschlossen_am", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_tickets_status", "tickets", ["status"])
    op.create_index("ix_tickets_zugewiesen_an_id", "tickets", ["zugewiesen_an_id"])
    op.create_index("ix_tickets_mitglied_id", "tickets", ["mitglied_id"])
    op.create_index("ix_tickets_absender_email", "tickets", ["absender_email"])

    op.create_table(
        "ticket_nachrichten",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("ticket_id", sa.String(36),
                  sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("richtung", sa.Enum("EINGEHEND", "AUSGEHEND", "INTERN", name="nachrichtrichtung"), nullable=False),
        sa.Column("inhalt", sa.Text(), nullable=False),
        sa.Column("verfasst_von_id", sa.String(36),
                  sa.ForeignKey("benutzer.id", ondelete="SET NULL"), nullable=True),
        sa.Column("erstellt_am", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_ticket_nachrichten_ticket_id", "ticket_nachrichten", ["ticket_id"])


def downgrade() -> None:
    op.drop_table("ticket_nachrichten")
    op.drop_table("tickets")
    sa.Enum(name="nachrichtrichtung").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="ticketstatus").drop(op.get_bind(), checkfirst=True)
