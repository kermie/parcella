"""Add groups, group memberships, and per-module permissions

Revision ID: 0038_groups_and_permissions
Revises: 0037_sample_data_records
Create Date: 2026-07-22

A simple ACL: users can belong to several groups, each group grants
read/write/delete per module (see app/permissions.py for the module
list and app/models.py for Group/GroupMembership/GroupModulePermission).
ADMIN/BOARD users are unaffected -- they keep their existing unconditional
access (see app/auth.py's require_admin) and never need a group.

Data migration: seeds one "Full Access" group with every permission on
every module, and adds every existing TREASURER/READONLY user to it, so
this ships without silently changing anyone's access -- new restrictions
only take effect once an admin creates a narrower group and moves a
user into it.
"""
from typing import Union
import uuid

from alembic import op
import sqlalchemy as sa

revision: str = "0038_groups_and_permissions"
down_revision: Union[str, None] = "0037_sample_data_records"
branch_labels = None
depends_on = None

MODULES = [
    "members_parcels", "work_hours", "water", "electricity",
    "insurance", "tickets", "purchase_requests", "calendar", "inventory",
]


def upgrade() -> None:
    op.create_table(
        "groups",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_table(
        "group_memberships",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("group_id", sa.String(36), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("user_id", "group_id", name="uq_group_membership"),
    )
    op.create_index("ix_group_memberships_user_id", "group_memberships", ["user_id"])
    op.create_index("ix_group_memberships_group_id", "group_memberships", ["group_id"])

    op.create_table(
        "group_module_permissions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("group_id", sa.String(36), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("module", sa.String(50), nullable=False),
        sa.Column("can_read", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("can_write", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("can_delete", sa.Boolean(), nullable=False, server_default="false"),
        sa.UniqueConstraint("group_id", "module", name="uq_group_module_permission"),
    )
    op.create_index("ix_group_module_permissions_group_id", "group_module_permissions", ["group_id"])

    connection = op.get_bind()

    group_id = str(uuid.uuid4())
    connection.execute(
        sa.text("INSERT INTO groups (id, name, description) VALUES (:id, :name, :description)"),
        {
            "id": group_id, "name": "Full Access",
            "description": "Created automatically when the group/permission system was introduced -- "
                            "grants every existing non-admin/board user the same access they already had.",
        },
    )
    for module in MODULES:
        connection.execute(
            sa.text(
                "INSERT INTO group_module_permissions (id, group_id, module, can_read, can_write, can_delete) "
                "VALUES (:id, :group_id, :module, true, true, true)"
            ),
            {"id": str(uuid.uuid4()), "group_id": group_id, "module": module},
        )

    existing_users = connection.execute(
        sa.text("SELECT id FROM users WHERE role IN ('TREASURER', 'READONLY')")
    ).fetchall()
    for (user_id,) in existing_users:
        connection.execute(
            sa.text(
                "INSERT INTO group_memberships (id, user_id, group_id) VALUES (:id, :user_id, :group_id)"
            ),
            {"id": str(uuid.uuid4()), "user_id": user_id, "group_id": group_id},
        )


def downgrade() -> None:
    op.drop_index("ix_group_module_permissions_group_id", table_name="group_module_permissions")
    op.drop_table("group_module_permissions")
    op.drop_index("ix_group_memberships_group_id", table_name="group_memberships")
    op.drop_index("ix_group_memberships_user_id", table_name="group_memberships")
    op.drop_table("group_memberships")
    op.drop_table("groups")
