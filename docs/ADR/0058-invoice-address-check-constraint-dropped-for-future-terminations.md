# Invoice-address CHECK constraint dropped: it broke future-dated terminations

**Context:** [ADR 0035](./0035-invoice-address-flag-on-member-parcel-assignments.md)
added `CHECK (NOT is_invoice_address OR assigned_until IS NULL)` on
`member_parcels`, reasoning that a former tenant (`assigned_until` set)
must never be flagged as the invoice address -- otherwise invoices keep
going to someone who's moved out. Every write path that sets
`assigned_until` also force-cleared `is_invoice_address` in the same
write, backed by this constraint as an always-true guarantee.

That reasoning silently over-reached: `assigned_until` set to a
**future** date means the tenant gave notice but hasn't actually moved
out yet -- [ADR 0052](./0052-member-parcel-is-current-property.md)
already established this exact case ("a future-dated termination isn't
in effect yet") for `MemberParcel.is_current`, used everywhere else a
tenancy's current status matters (meeting sign-in sheets, tenant
listings). The invoice-address write paths and the CHECK constraint
never got the same treatment. Confirmed in production (issue #172): a
still-occupied parcel with a termination scheduled months out silently
stopped appearing in invoice runs the moment the future date was
recorded, because the write path zeroed `is_invoice_address` immediately
and the constraint would have rejected setting it back to `true` while
`assigned_until` was non-null, future or not.

**Decision: make every write and read path `is_current`-aware, and
drop the constraint rather than trying to fix it.**

- `app/routers/parcels.py`'s `member_assignment_update` and
  `app/routers/api_parcels.py`'s assignment-create endpoint now clear
  `is_invoice_address` only when the tenancy is no longer current
  (`not assignment.is_current`), not whenever `assigned_until` is set
  at all.
- `app/invoice_generation.py`'s `_parcel_is_billable` and the
  invoice-address-member lookup now filter on `MemberParcel.is_current`
  instead of `assigned_until is None`.
- `app/invoice_delivery.py`'s email-recipient lookup now uses
  `current_tenant_filter()` (`app/database.py`) instead of
  `assigned_until.is_(None)`, for the same reason -- otherwise a fixed
  billing computation would still fail to find anyone to email the
  invoice to.
- The CHECK constraint (migration 0069) is dropped outright rather than
  rewritten to reference "today": Postgres CHECK constraints are only
  evaluated at write time, not continuously, so there is no immutable
  expression that captures "is_current" the way a live query or Python
  property can. Keeping a constraint that's silently wrong for the
  future-dated case would be worse than removing it.

**Consequence, accepted:** the DB no longer independently guarantees
that a truly-past-ended tenancy can't have `is_invoice_address=true`
stored on it. This is safe in practice because every consumer now
re-derives currentness live (`is_current` / `current_tenant_filter()`)
rather than trusting the stored flag in isolation -- a stale `true` on
a long-past assignment is simply never read as "current" by anything,
so it has no effect on billing, email delivery, or any other invoice-
address consumer. The correctness guarantee moved from "the database
enforces this on every write" to "every read path re-checks currentness
live" -- deliberately, since only the latter can express a
time-dependent rule at all.
