"""Drop ck_invoice_address_only_for_current_tenants (issue #172)

Revision ID: 0069_drop_invoice_address_check
Revises: 0068_general_levy_pricing_mode
Create Date: 2026-08-01

ADR 0035's CHECK constraint (added in migration 0036) rejected
is_invoice_address=true whenever assigned_until was set at all -- which
also blocked a future-dated termination (a tenant who gave notice but
hasn't actually moved out yet, per MemberParcel.is_current/ADR 0052).
In production this meant a still-occupied parcel with a scheduled
future move-out silently stopped being billed the moment the notice
date was recorded (issue #172).

A CHECK constraint can't reference "today" safely -- it's evaluated
only at write time, not continuously, so it can't express "is_current"
the way application code can. Rather than leaving a stale, misleading
constraint in place, this drops it: correctness now relies entirely on
every read path (app/invoice_generation.py's _parcel_is_billable,
app/invoice_delivery.py's recipient lookup) using
MemberParcel.is_current / current_tenant_filter() rather than trusting
a stored is_invoice_address in isolation. See ADR 0058 for the full
writeup.
"""
from typing import Union

from alembic import op

revision: str = "0069_drop_invoice_address_check"
down_revision: Union[str, None] = "0068_general_levy_pricing_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_invoice_address_only_for_current_tenants", "member_parcels", type_="check"
    )


def downgrade() -> None:
    op.create_check_constraint(
        "ck_invoice_address_only_for_current_tenants",
        "member_parcels",
        "NOT is_invoice_address OR assigned_until IS NULL",
    )
