"""Fix invoicepricingmode enum: COMMUNAL_AREA_SHARE was added lowercase

Revision ID: 0063_fix_communal_share_enum
Revises: 0062_finance_accounts
Create Date: 2026-07-31

Issue #160: adding a new item with pricing_mode=COMMUNAL_AREA_SHARE
crashed with `invalid input value for enum invoicepricingmode:
"COMMUNAL_AREA_SHARE"`. Migration 0052's own file already has the
correct uppercase `ALTER TYPE ... ADD VALUE 'COMMUNAL_AREA_SHARE'`, but
the real database's enum type somehow ended up with the lowercase
`communal_area_share` label instead (SQLAlchemy's Enum column stores
the Python member NAME, uppercase, never the lowercase `.value` -- see
CLAUDE.md's documented sharp edge from the 0053 incident). Postgres has
no `ALTER TYPE ... DROP VALUE` to remove a wrong label directly, so
this rebuilds the type from scratch: rename old -> create new with the
correct 8 uppercase labels -> cast both columns over -> drop old type.
Safe because nothing in the real data used the stray lowercase value
(confirmed via SELECT count(*) ... WHERE pricing_mode::text =
'communal_area_share' on both tables -- zero rows).
"""
from typing import Union

from alembic import op

revision: str = "0063_fix_communal_share_enum"
down_revision: Union[str, None] = "0062_finance_accounts"
branch_labels = None
depends_on = None

_LABELS = (
    "FIXED_PER_PARCEL", "FIXED_PER_PERSON", "PER_SQM", "WATER_USAGE",
    "ELECTRICITY_USAGE", "INSURANCE_COST", "COMMUNAL_AREA_SHARE", "WORK_HOURS_SHORTFALL",
)


def upgrade() -> None:
    op.execute("ALTER TYPE invoicepricingmode RENAME TO invoicepricingmode_old")
    op.execute(
        "CREATE TYPE invoicepricingmode AS ENUM (" + ", ".join(f"'{label}'" for label in _LABELS) + ")"
    )
    for table in ("invoice_item_templates", "invoice_item_definitions"):
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN pricing_mode TYPE invoicepricingmode "
            f"USING pricing_mode::text::invoicepricingmode"
        )
    op.execute("DROP TYPE invoicepricingmode_old")


def downgrade() -> None:
    # Reintroducing the stray lowercase label serves no purpose --
    # nothing to downgrade to. Same "not implemented" stance as 0052's
    # own downgrade.
    pass
