"""Drop invoice_runs.starting_sequence_override

Revision ID: 0045_drop_run_starting_override
Revises: 0044_invoice_run_starting_number
Create Date: 2026-07-24

Per-run override (issue #73) is being replaced by a single global,
one-shot override read from ClubSetting "invoice_number_start" (see
app/invoice_generation.py's _first_invoice_sequence) -- the user found
a per-run field in /finances/runs confusing and wanted the existing
/admin/settings "starting invoice number" field to just do the job
directly instead. Safe to drop unconditionally: the only real run that
ever had this set is already FINALIZED, so its invoice numbers are
already permanent and don't depend on this column anymore.
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0045_drop_run_starting_override"
down_revision: Union[str, None] = "0044_invoice_run_starting_number"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("invoice_runs", "starting_sequence_override")


def downgrade() -> None:
    op.add_column("invoice_runs", sa.Column("starting_sequence_override", sa.Integer(), nullable=True))
