"""Ticketsystem auf Englisch umstellen (Ticket/TicketMessage)

Revision ID: 0018_english_tickets
Revises: 0017_english_insurance
Create Date: 2026-07-12
"""
from typing import Union
from alembic import op
import sqlalchemy as sa

revision: str = "0018_english_tickets"
down_revision: Union[str, None] = "0017_english_insurance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -----------------------------------------------------------------
    # 1. Enum-Typen umbenennen + Werte aktualisieren
    # -----------------------------------------------------------------
    op.execute("ALTER TYPE ticketstatus RENAME TO ticketstatus_old")
    op.execute("CREATE TYPE ticketstatus AS ENUM ('UNASSIGNED', 'ASSIGNED', 'DEFERRED', 'CLOSED')")
    op.execute("""
        ALTER TABLE tickets ALTER COLUMN status TYPE ticketstatus USING (
            CASE status::text
                WHEN 'NICHT_ZUGEWIESEN' THEN 'UNASSIGNED'
                WHEN 'ZUGEWIESEN' THEN 'ASSIGNED'
                WHEN 'ZURUECKGESTELLT' THEN 'DEFERRED'
                WHEN 'GESCHLOSSEN' THEN 'CLOSED'
                ELSE 'UNASSIGNED'
            END
        )::ticketstatus
    """)
    op.execute("DROP TYPE ticketstatus_old")

    op.execute("ALTER TYPE nachrichtrichtung RENAME TO messagedirection_old")
    op.execute("CREATE TYPE messagedirection AS ENUM ('INCOMING', 'OUTGOING', 'INTERNAL')")
    op.execute("""
        ALTER TABLE ticket_nachrichten ALTER COLUMN richtung TYPE messagedirection USING (
            CASE richtung::text
                WHEN 'EINGEHEND' THEN 'INCOMING'
                WHEN 'AUSGEHEND' THEN 'OUTGOING'
                WHEN 'INTERN' THEN 'INTERNAL'
                ELSE 'INCOMING'
            END
        )::messagedirection
    """)
    op.execute("DROP TYPE messagedirection_old")

    # -----------------------------------------------------------------
    # 2. Tabellen umbenennen
    # -----------------------------------------------------------------
    op.rename_table("ticket_nachrichten", "ticket_messages")

    # -----------------------------------------------------------------
    # 3. Spalten in tickets umbenennen
    # -----------------------------------------------------------------
    op.alter_column("tickets", "betreff", new_column_name="subject")
    op.alter_column("tickets", "zugewiesen_an_id", new_column_name="assigned_to_id")
    op.alter_column("tickets", "zurueckgestellt_bis", new_column_name="deferred_until")
    op.alter_column("tickets", "mitglied_id", new_column_name="member_id")
    op.alter_column("tickets", "absender_email", new_column_name="sender_email")
    op.alter_column("tickets", "absender_name", new_column_name="sender_name")
    op.alter_column("tickets", "spam_verdacht", new_column_name="spam_suspected")
    op.alter_column("tickets", "spam_begruendung", new_column_name="spam_reasoning")
    op.alter_column("tickets", "erstellt_am", new_column_name="created_at")
    op.alter_column("tickets", "aktualisiert_am", new_column_name="updated_at")
    op.alter_column("tickets", "geschlossen_am", new_column_name="closed_at")

    op.execute("ALTER INDEX IF EXISTS ix_tickets_zugewiesen_an_id RENAME TO ix_tickets_assigned_to_id")
    op.execute("ALTER INDEX IF EXISTS ix_tickets_mitglied_id RENAME TO ix_tickets_member_id")
    op.execute("ALTER INDEX IF EXISTS ix_tickets_absender_email RENAME TO ix_tickets_sender_email")

    # -----------------------------------------------------------------
    # 4. Spalten in ticket_messages umbenennen
    # -----------------------------------------------------------------
    op.alter_column("ticket_messages", "richtung", new_column_name="direction")
    op.alter_column("ticket_messages", "inhalt", new_column_name="content")
    op.alter_column("ticket_messages", "verfasst_von_id", new_column_name="authored_by_id")
    op.alter_column("ticket_messages", "erstellt_am", new_column_name="created_at")

    op.execute("ALTER INDEX IF EXISTS ix_ticket_nachrichten_ticket_id RENAME TO ix_ticket_messages_ticket_id")
    op.execute("ALTER INDEX IF EXISTS ix_ticket_nachrichten_message_id RENAME TO ix_ticket_messages_message_id")


def downgrade() -> None:
    op.execute("ALTER INDEX IF EXISTS ix_ticket_messages_message_id RENAME TO ix_ticket_nachrichten_message_id")
    op.execute("ALTER INDEX IF EXISTS ix_ticket_messages_ticket_id RENAME TO ix_ticket_nachrichten_ticket_id")

    op.alter_column("ticket_messages", "created_at", new_column_name="erstellt_am")
    op.alter_column("ticket_messages", "authored_by_id", new_column_name="verfasst_von_id")
    op.alter_column("ticket_messages", "content", new_column_name="inhalt")
    op.alter_column("ticket_messages", "direction", new_column_name="richtung")

    op.execute("ALTER INDEX IF EXISTS ix_tickets_sender_email RENAME TO ix_tickets_absender_email")
    op.execute("ALTER INDEX IF EXISTS ix_tickets_member_id RENAME TO ix_tickets_mitglied_id")
    op.execute("ALTER INDEX IF EXISTS ix_tickets_assigned_to_id RENAME TO ix_tickets_zugewiesen_an_id")

    op.alter_column("tickets", "closed_at", new_column_name="geschlossen_am")
    op.alter_column("tickets", "updated_at", new_column_name="aktualisiert_am")
    op.alter_column("tickets", "created_at", new_column_name="erstellt_am")
    op.alter_column("tickets", "spam_reasoning", new_column_name="spam_begruendung")
    op.alter_column("tickets", "spam_suspected", new_column_name="spam_verdacht")
    op.alter_column("tickets", "sender_name", new_column_name="absender_name")
    op.alter_column("tickets", "sender_email", new_column_name="absender_email")
    op.alter_column("tickets", "member_id", new_column_name="mitglied_id")
    op.alter_column("tickets", "deferred_until", new_column_name="zurueckgestellt_bis")
    op.alter_column("tickets", "assigned_to_id", new_column_name="zugewiesen_an_id")
    op.alter_column("tickets", "subject", new_column_name="betreff")

    op.rename_table("ticket_messages", "ticket_nachrichten")

    op.execute("ALTER TYPE messagedirection RENAME TO messagedirection_old")
    op.execute("CREATE TYPE nachrichtrichtung AS ENUM ('EINGEHEND', 'AUSGEHEND', 'INTERN')")
    op.execute("""
        ALTER TABLE ticket_nachrichten ALTER COLUMN richtung TYPE nachrichtrichtung USING (
            CASE richtung::text
                WHEN 'INCOMING' THEN 'EINGEHEND'
                WHEN 'OUTGOING' THEN 'AUSGEHEND'
                WHEN 'INTERNAL' THEN 'INTERN'
                ELSE 'EINGEHEND'
            END
        )::nachrichtrichtung
    """)
    op.execute("DROP TYPE messagedirection_old")

    op.execute("ALTER TYPE ticketstatus RENAME TO ticketstatus_old")
    op.execute("CREATE TYPE ticketstatus AS ENUM ('NICHT_ZUGEWIESEN', 'ZUGEWIESEN', 'ZURUECKGESTELLT', 'GESCHLOSSEN')")
    op.execute("""
        ALTER TABLE tickets ALTER COLUMN status TYPE ticketstatus USING (
            CASE status::text
                WHEN 'UNASSIGNED' THEN 'NICHT_ZUGEWIESEN'
                WHEN 'ASSIGNED' THEN 'ZUGEWIESEN'
                WHEN 'DEFERRED' THEN 'ZURUECKGESTELLT'
                WHEN 'CLOSED' THEN 'GESCHLOSSEN'
                ELSE 'NICHT_ZUGEWIESEN'
            END
        )::ticketstatus
    """)
    op.execute("DROP TYPE ticketstatus_old")
