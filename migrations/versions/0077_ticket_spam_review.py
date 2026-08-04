"""Add tickets.spam_reviewed_by_id / spam_reviewed_at

Revision ID: 0077_spam_review
Revises: 0076_must_change_pw
Create Date: 2026-08-05

Tracks whether a human ever made a spam_suspected decision on a ticket
(mark-spam/not-spam buttons, their bulk equivalents, or the API's PUT
/spam-status), as opposed to the automated check. Lets the new bulk
backlog re-scan (POST /tickets/rescan-spam) skip any ticket a person
already decided on, instead of potentially re-flagging a deliberate
staff override.
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0077_spam_review"
down_revision: Union[str, None] = "0076_must_change_pw"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tickets",
        sa.Column("spam_reviewed_by_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column("tickets", sa.Column("spam_reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_tickets_spam_reviewed_by_id", "tickets", ["spam_reviewed_by_id"])


def downgrade() -> None:
    op.drop_index("ix_tickets_spam_reviewed_by_id", table_name="tickets")
    op.drop_column("tickets", "spam_reviewed_at")
    op.drop_column("tickets", "spam_reviewed_by_id")
