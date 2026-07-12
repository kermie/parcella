"""Einkaufswuensche-Modul auf Englisch umstellen (PurchaseRequest/PurchaseRequestApproval)

Revision ID: 0019_english_purchase_requests
Revises: 0018_english_tickets
Create Date: 2026-07-12
"""
from typing import Union
from alembic import op
import sqlalchemy as sa

revision: str = "0019_english_purchase_requests"
down_revision: Union[str, None] = "0018_english_tickets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -----------------------------------------------------------------
    # 1. Enum-Typ umbenennen + Werte aktualisieren
    # -----------------------------------------------------------------
    op.execute("ALTER TYPE einkaufswunschstatus RENAME TO purchaserequeststatus_old")
    op.execute("CREATE TYPE purchaserequeststatus AS ENUM ('OPEN', 'APPROVED', 'REJECTED')")
    op.execute("""
        ALTER TABLE einkaufswuensche ALTER COLUMN status TYPE purchaserequeststatus USING (
            CASE status::text
                WHEN 'OFFEN' THEN 'OPEN'
                WHEN 'GENEHMIGT' THEN 'APPROVED'
                WHEN 'ABGELEHNT' THEN 'REJECTED'
                ELSE 'OPEN'
            END
        )::purchaserequeststatus
    """)
    op.execute("DROP TYPE purchaserequeststatus_old")

    # -----------------------------------------------------------------
    # 2. Tabellen umbenennen
    # -----------------------------------------------------------------
    op.rename_table("einkaufswuensche", "purchase_requests")
    op.rename_table("einkaufswunsch_freigaben", "purchase_request_approvals")

    # -----------------------------------------------------------------
    # 3. Spalten in purchase_requests umbenennen
    # -----------------------------------------------------------------
    op.alter_column("purchase_requests", "titel", new_column_name="title")
    op.alter_column("purchase_requests", "begruendung", new_column_name="justification")
    op.alter_column("purchase_requests", "geschaetzte_kosten_eur", new_column_name="estimated_cost_eur")
    op.alter_column("purchase_requests", "angefragt_von_id", new_column_name="requested_by_id")
    op.alter_column("purchase_requests", "anfragender_name", new_column_name="requester_name")
    op.alter_column("purchase_requests", "anfragender_email", new_column_name="requester_email")
    op.alter_column("purchase_requests", "erstellt_von_id", new_column_name="created_by_id")
    op.alter_column("purchase_requests", "bestaetigungs_token", new_column_name="confirmation_token")
    op.alter_column("purchase_requests", "vom_anfragenden_bestaetigt", new_column_name="confirmed_by_requester")
    op.alter_column(
        "purchase_requests", "vom_anfragenden_bestaetigt_am", new_column_name="confirmed_by_requester_at"
    )
    op.alter_column("purchase_requests", "ablehnungsgrund", new_column_name="rejection_reason")
    op.alter_column("purchase_requests", "abgelehnt_von_id", new_column_name="rejected_by_id")
    op.alter_column("purchase_requests", "abgelehnt_am", new_column_name="rejected_at")
    op.alter_column("purchase_requests", "genehmigt_am", new_column_name="approved_at")
    op.alter_column("purchase_requests", "erstellt_am", new_column_name="created_at")
    op.alter_column("purchase_requests", "aktualisiert_am", new_column_name="updated_at")

    op.execute("ALTER INDEX IF EXISTS ix_einkaufswuensche_status RENAME TO ix_purchase_requests_status")

    # -----------------------------------------------------------------
    # 4. Spalten in purchase_request_approvals umbenennen
    # -----------------------------------------------------------------
    op.alter_column("purchase_request_approvals", "einkaufswunsch_id", new_column_name="purchase_request_id")
    op.alter_column("purchase_request_approvals", "benutzer_id", new_column_name="user_id")
    op.alter_column("purchase_request_approvals", "freigegeben_am", new_column_name="approved_at")

    op.execute(
        "ALTER INDEX IF EXISTS ix_einkaufswunsch_freigaben_ew_id "
        "RENAME TO ix_purchase_request_approvals_purchase_request_id"
    )
    op.execute(
        "ALTER INDEX IF EXISTS ix_einkaufswunsch_freigaben_benutzer_id "
        "RENAME TO ix_purchase_request_approvals_user_id"
    )
    op.execute(
        "ALTER TABLE purchase_request_approvals "
        "RENAME CONSTRAINT uq_einkaufswunsch_freigabe TO uq_purchase_request_approval"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE purchase_request_approvals "
        "RENAME CONSTRAINT uq_purchase_request_approval TO uq_einkaufswunsch_freigabe"
    )
    op.execute(
        "ALTER INDEX IF EXISTS ix_purchase_request_approvals_user_id "
        "RENAME TO ix_einkaufswunsch_freigaben_benutzer_id"
    )
    op.execute(
        "ALTER INDEX IF EXISTS ix_purchase_request_approvals_purchase_request_id "
        "RENAME TO ix_einkaufswunsch_freigaben_ew_id"
    )

    op.alter_column("purchase_request_approvals", "approved_at", new_column_name="freigegeben_am")
    op.alter_column("purchase_request_approvals", "user_id", new_column_name="benutzer_id")
    op.alter_column("purchase_request_approvals", "purchase_request_id", new_column_name="einkaufswunsch_id")

    op.execute("ALTER INDEX IF EXISTS ix_purchase_requests_status RENAME TO ix_einkaufswuensche_status")

    op.alter_column("purchase_requests", "updated_at", new_column_name="aktualisiert_am")
    op.alter_column("purchase_requests", "created_at", new_column_name="erstellt_am")
    op.alter_column("purchase_requests", "approved_at", new_column_name="genehmigt_am")
    op.alter_column("purchase_requests", "rejected_at", new_column_name="abgelehnt_am")
    op.alter_column("purchase_requests", "rejected_by_id", new_column_name="abgelehnt_von_id")
    op.alter_column("purchase_requests", "rejection_reason", new_column_name="ablehnungsgrund")
    op.alter_column(
        "purchase_requests", "confirmed_by_requester_at", new_column_name="vom_anfragenden_bestaetigt_am"
    )
    op.alter_column("purchase_requests", "confirmed_by_requester", new_column_name="vom_anfragenden_bestaetigt")
    op.alter_column("purchase_requests", "confirmation_token", new_column_name="bestaetigungs_token")
    op.alter_column("purchase_requests", "created_by_id", new_column_name="erstellt_von_id")
    op.alter_column("purchase_requests", "requester_email", new_column_name="anfragender_email")
    op.alter_column("purchase_requests", "requester_name", new_column_name="anfragender_name")
    op.alter_column("purchase_requests", "requested_by_id", new_column_name="angefragt_von_id")
    op.alter_column("purchase_requests", "estimated_cost_eur", new_column_name="geschaetzte_kosten_eur")
    op.alter_column("purchase_requests", "justification", new_column_name="begruendung")
    op.alter_column("purchase_requests", "title", new_column_name="titel")

    op.rename_table("purchase_request_approvals", "einkaufswunsch_freigaben")
    op.rename_table("purchase_requests", "einkaufswuensche")

    op.execute("ALTER TYPE purchaserequeststatus RENAME TO purchaserequeststatus_old")
    op.execute("CREATE TYPE einkaufswunschstatus AS ENUM ('OFFEN', 'GENEHMIGT', 'ABGELEHNT')")
    op.execute("""
        ALTER TABLE einkaufswuensche ALTER COLUMN status TYPE einkaufswunschstatus USING (
            CASE status::text
                WHEN 'OPEN' THEN 'OFFEN'
                WHEN 'APPROVED' THEN 'GENEHMIGT'
                WHEN 'REJECTED' THEN 'ABGELEHNT'
                ELSE 'OFFEN'
            END
        )::einkaufswunschstatus
    """)
    op.execute("DROP TYPE purchaserequeststatus_old")
