"""Add announcements module: announcements, announcement_deliveries

Revision ID: 0029_announcements
Revises: 0028_drop_signup_tables
Create Date: 2026-07-20

Foundation for the announcements module: authoring a single piece of
club news (Markdown source + derived/cached HTML + optional image +
optional print-only shortened text) and tracking its delivery status
across up to three channels (blog draft via CMS API, member email,
printable PDF one-pager).

No changes to existing tables.
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0029_announcements"
down_revision: Union[str, None] = "0028_drop_signup_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "announcements",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("body_markdown", sa.Text(), nullable=False),
        sa.Column("body_html", sa.Text(), nullable=False, server_default=""),
        sa.Column("image_filename", sa.String(255), nullable=True),
        sa.Column("print_text_override", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("DRAFT", "PUBLISHED", "ARCHIVED", name="announcementstatus"),
            nullable=False,
            server_default="DRAFT",
        ),
        sa.Column(
            "created_by_id", sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "announcement_deliveries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "announcement_id", sa.String(36),
            sa.ForeignKey("announcements.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "channel",
            sa.Enum("BLOG", "EMAIL", "PRINT", name="announcementchannel"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("PENDING", "SENT", "FAILED", name="announcementdeliverystatus"),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("external_reference", sa.String(500), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("announcement_id", "channel", name="uq_announcement_delivery_channel"),
    )
    op.create_index(
        "ix_announcement_deliveries_announcement_id",
        "announcement_deliveries", ["announcement_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_announcement_deliveries_announcement_id", table_name="announcement_deliveries")
    op.drop_table("announcement_deliveries")
    op.drop_table("announcements")
    op.execute("DROP TYPE IF EXISTS announcementdeliverystatus")
    op.execute("DROP TYPE IF EXISTS announcementchannel")
    op.execute("DROP TYPE IF EXISTS announcementstatus")
