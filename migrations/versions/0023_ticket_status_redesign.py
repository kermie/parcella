"""Redesign ticket status set: ACTIVE/ASSIGNED/WAITING/POSTPONED/CLOSED/DELETED

Revision ID: 0023_ticket_status_redesign
Revises: 0022_remove_tenant_role
Create Date: 2026-07-16

The board wants a richer ticket status set than the original four
(UNASSIGNED/ASSIGNED/DEFERRED/CLOSED):

  - UNASSIGNED  -> ACTIVE     (renamed; a ticket nobody has claimed yet)
  - ASSIGNED    -> ASSIGNED   (unchanged)
  - DEFERRED    -> POSTPONED  (renamed, same "hidden until a date" idea)
  - CLOSED      -> CLOSED     (unchanged)
  - (new)          WAITING    (waiting on the sender's reply, no date --
                                manually set, auto-clears to ACTIVE/ASSIGNED
                                the moment a new reply comes in)
  - (new)          DELETED    (soft-delete for tickets, same idea as
                                Member.deleted_at, but modeled as a status
                                here since Ticket didn't have a separate
                                deleted_at column)

Also renames the `deferred_until` column to `postponed_until` to match
the new status name -- see app/routers/tickets.py and app/ticket_mailer.py
for the accompanying behavior changes (postponed tickets hidden from the
active list until due, and any incoming reply reactivates a
POSTPONED/WAITING/CLOSED ticket, not just CLOSED as before).
"""
from typing import Union

from alembic import op

revision: str = "0023_ticket_status_redesign"
down_revision: Union[str, None] = "0022_remove_tenant_role"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE ticketstatus RENAME TO ticketstatus_old")
    op.execute(
        "CREATE TYPE ticketstatus AS ENUM "
        "('ACTIVE', 'ASSIGNED', 'WAITING', 'POSTPONED', 'CLOSED', 'DELETED')"
    )
    op.execute("""
        ALTER TABLE tickets ALTER COLUMN status TYPE ticketstatus USING (
            CASE status::text
                WHEN 'UNASSIGNED' THEN 'ACTIVE'
                WHEN 'ASSIGNED' THEN 'ASSIGNED'
                WHEN 'DEFERRED' THEN 'POSTPONED'
                WHEN 'CLOSED' THEN 'CLOSED'
                ELSE 'ACTIVE'
            END
        )::ticketstatus
    """)
    op.execute("DROP TYPE ticketstatus_old")

    op.execute("ALTER TABLE tickets ALTER COLUMN status SET DEFAULT 'ACTIVE'")
    op.alter_column("tickets", "deferred_until", new_column_name="postponed_until")


def downgrade() -> None:
    op.alter_column("tickets", "postponed_until", new_column_name="deferred_until")

    op.execute("ALTER TYPE ticketstatus RENAME TO ticketstatus_old")
    op.execute(
        "CREATE TYPE ticketstatus AS ENUM "
        "('UNASSIGNED', 'ASSIGNED', 'DEFERRED', 'CLOSED')"
    )
    op.execute("""
        ALTER TABLE tickets ALTER COLUMN status TYPE ticketstatus USING (
            CASE status::text
                WHEN 'ACTIVE' THEN 'UNASSIGNED'
                WHEN 'ASSIGNED' THEN 'ASSIGNED'
                WHEN 'WAITING' THEN 'ASSIGNED'
                WHEN 'POSTPONED' THEN 'DEFERRED'
                WHEN 'CLOSED' THEN 'CLOSED'
                WHEN 'DELETED' THEN 'CLOSED'
                ELSE 'UNASSIGNED'
            END
        )::ticketstatus
    """)
    op.execute("DROP TYPE ticketstatus_old")
    op.execute("ALTER TABLE tickets ALTER COLUMN status SET DEFAULT 'UNASSIGNED'")
