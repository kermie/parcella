"""Add external_id to announcement_deliveries

Revision ID: 0031_delivery_external_id
Revises: 0030_announcement_sending_status
Create Date: 2026-07-20

Supports the print channel: rather than storing a WordPress post's
public URL at draft-creation time (which could go stale -- the post
might not be published yet, or its slug could change before
publishing), this stores just the WordPress post ID. The print channel
asks WordPress directly, at PDF-generation time, whether the post has
since been published and what its current public URL is.

Purely additive; no existing data affected.
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0031_delivery_external_id"
down_revision: Union[str, None] = "0030_announcement_sending_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("announcement_deliveries", sa.Column("external_id", sa.String(100), nullable=True))


def downgrade() -> None:
    op.drop_column("announcement_deliveries", "external_id")
