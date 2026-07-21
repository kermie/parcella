"""Add parcel_cloud_folders table for the cloud storage connector

Revision ID: 0033_parcel_cloud_folders
Revises: 0032_inventory
Create Date: 2026-07-21

Foundation for connecting Parcella to a club's cloud storage backend
(Nextcloud first, via app/cloud_storage.py). This table tracks which
folder path is currently assigned to each parcel; see app/models.py
(ParcelCloudFolder) and app/parcel_cloud_folders.py for the full
reasoning, in particular why this is scoped to the parcel rather than
to a single member_parcels row, and why only one row per parcel may be
active at a time.

Purely additive; no existing data affected.
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0033_parcel_cloud_folders"
down_revision: Union[str, None] = "0032_inventory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "parcel_cloud_folders",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "parcel_id", sa.String(36),
            sa.ForeignKey("parcels.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("relative_path", sa.String(500), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "set_by_user_id", sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_parcel_cloud_folders_parcel_id", "parcel_cloud_folders", ["parcel_id"],
    )
    # Enforces "at most one active folder per parcel" at the database
    # level, not just in application logic.
    op.create_index(
        "uq_parcel_cloud_folders_one_active_per_parcel", "parcel_cloud_folders", ["parcel_id"],
        unique=True, postgresql_where=sa.text("is_active = true"),
    )


def downgrade() -> None:
    op.drop_index("uq_parcel_cloud_folders_one_active_per_parcel", table_name="parcel_cloud_folders")
    op.drop_index("ix_parcel_cloud_folders_parcel_id", table_name="parcel_cloud_folders")
    op.drop_table("parcel_cloud_folders")
