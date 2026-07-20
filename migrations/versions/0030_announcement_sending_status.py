"""Add SENDING value to announcementdeliverystatus enum

Revision ID: 0030_announcement_sending_status
Revises: 0029_announcements
Create Date: 2026-07-20

Supports pacing the email channel's send (a fixed number of emails per
minute rather than all at once) as a background task: the delivery row
is marked SENDING immediately so the UI can show "in progress" while
the paced send runs, then flips to SENT/FAILED once it's done.

Postgres requires ALTER TYPE ... ADD VALUE to run outside a
transaction block (a new enum value can't be used in the same
transaction that creates it), hence the autocommit_block() below.
"""
from typing import Union

from alembic import op

revision: str = "0030_announcement_sending_status"
down_revision: Union[str, None] = "0029_announcements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE announcementdeliverystatus ADD VALUE IF NOT EXISTS 'SENDING'")


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE. Removing an enum value
    # cleanly requires rebuilding the type (rename old, create new
    # without SENDING, cast the column, drop old) -- not implemented
    # here since nothing in this project relies on downgrading past
    # this point in normal operation. Any announcement_deliveries row
    # left with status='SENDING' would need to be resolved to SENT or
    # FAILED by hand before attempting a real downgrade.
    pass
