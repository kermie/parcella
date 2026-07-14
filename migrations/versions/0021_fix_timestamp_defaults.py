"""Fix missing server_default on timestamp columns from baseline schema

Revision ID: 0021_fix_timestamp_defaults
Revises: 0020_english_core_users
Create Date: 2026-07-14

The baseline migration (0001_initial) created several tables with
`created_at`/`updated_at` as NOT NULL but without a database-level
DEFAULT, even though the SQLAlchemy models declare
`server_default=func.now()`. Existing installations never hit this,
because their schema was originally created via
`Base.metadata.create_all()` (which does apply the default) and 0001
was later applied to them with `alembic stamp head` rather than being
executed. The test suite also builds tables via `metadata.create_all`,
bypassing Alembic entirely.

A genuinely fresh install that runs `alembic upgrade head` against an
empty database gets a schema WITHOUT the default, and the app then
fails on first startup with a NotNullViolationError on users.created_at
when it tries to insert the first admin row.

This migration adds the missing DEFAULT now() at the database level to
match what the ORM models have always declared.
"""
from typing import Union

from alembic import op

revision: str = "0021_fix_timestamp_defaults"
down_revision: Union[str, None] = "0020_english_core_users"
branch_labels = None
depends_on = None

# (table, column) pairs missing a server-side default since 0001_initial
_AFFECTED_COLUMNS = [
    ("users", "created_at"),
    ("users", "updated_at"),
    ("invitations", "created_at"),
    ("members", "created_at"),
    ("members", "updated_at"),
    ("parcels", "created_at"),
    ("parcels", "updated_at"),
    ("member_parcels", "created_at"),
    ("club_settings", "updated_at"),
]


def upgrade() -> None:
    for table, column in _AFFECTED_COLUMNS:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} SET DEFAULT now()")


def downgrade() -> None:
    for table, column in _AFFECTED_COLUMNS:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} DROP DEFAULT")
