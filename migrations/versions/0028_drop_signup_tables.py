"""Drop public_session_signups and public_session_signup_sessions.

Revision ID: 0028_drop_signup_tables
Revises: 0027_public_signup_api
Create Date: 2026-07-19

Design change: public signups now create real SessionParticipation rows
directly (status REGISTERED) rather than a parallel PublicSessionSignup
structure. Reasoning: the club's public website must not expose exact
member names (privacy requirement), so an external visitor can only
identify by parcel number -- Parcella now does name-matching against
current parcel residents server-side, or registers every current
resident of the parcel when it can't confidently match, letting the
board delete the wrong ones from the normal participants table. See
docs/module-public-api.md.
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0028_drop_signup_tables"
down_revision: Union[str, None] = "0027_public_signup_api"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_public_session_signup_sessions_session_id", table_name="public_session_signup_sessions")
    op.drop_index("ix_public_session_signup_sessions_signup_id", table_name="public_session_signup_sessions")
    op.drop_table("public_session_signup_sessions")
    op.drop_index("ix_public_session_signups_parcel_id", table_name="public_session_signups")
    op.drop_table("public_session_signups")


def downgrade() -> None:
    op.create_table(
        "public_session_signups",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "parcel_id", sa.String(36),
            sa.ForeignKey("parcels.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("remarks", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_public_session_signups_parcel_id", "public_session_signups", ["parcel_id"])

    op.create_table(
        "public_session_signup_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "signup_id", sa.String(36),
            sa.ForeignKey("public_session_signups.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "session_id", sa.String(36),
            sa.ForeignKey("work_sessions.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.UniqueConstraint("signup_id", "session_id", name="uq_public_signup_session"),
    )
    op.create_index("ix_public_session_signup_sessions_signup_id", "public_session_signup_sessions", ["signup_id"])
    op.create_index("ix_public_session_signup_sessions_session_id", "public_session_signup_sessions", ["session_id"])
