"""Add users.must_change_password (security review)

Revision ID: 0076_must_change_pw
Revises: 0075_txn_counterparty
Create Date: 2026-08-02

Forces the bootstrap admin created at first startup
(admin@parcella.local, with the documented default password) to set a
new password before the account can be used.

Existing installations are NOT flagged: the column defaults to false for
every row that already exists, so nobody is locked out of a running
instance by an upgrade. Only accounts created by the first-start
bootstrap after this migration get the flag.
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0076_must_change_pw"
down_revision: Union[str, None] = "0075_txn_counterparty"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "must_change_password")
