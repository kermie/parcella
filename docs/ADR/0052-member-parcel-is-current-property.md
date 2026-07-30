# `MemberParcel.is_current`: a future-dated termination isn't in effect yet

**Context:** Issue #130 -- a board member terminates a tenancy ahead of
time by setting `assigned_until` to a future date (notice given, contract
ends at the end of the season). Every consumer of "who's the current
tenant" (the parcel list/detail pages, the announcement email channel,
the general-meeting sign-in sheet, the work-hours attendee sheet,
insurance household grouping, the public signup API, the cloud-folder
vacancy check) tested `assigned_until IS NULL` only, with no comparison
to today's date. The moment the future date was saved, the tenant was
treated as already gone everywhere, even though the termination hadn't
taken effect.

**Fix: `MemberParcel.is_current`, mirroring `Member.is_active`.** `Member`
already has exactly this shape of problem solved for membership expiry
(`Member.is_active`: not soft-deleted and `member_until` is `NULL` or in
the future), plus a query-level helper, `active_member_filter()` in
`app/database.py`, for SQL call sites. `MemberParcel` gets the same two
pieces: an `is_current` property on the model, and `current_tenant_filter()`
in `app/database.py`. In-Python filtering of an already-loaded relationship
(e.g. `member.parcel_assignments`) uses the property; a `.where()` clause
uses the helper.

**Strict `>`, not `>=` -- deliberately different from `Member.is_active`.**
`Member.is_active` treats `member_until` with `>=`: a membership ending
today is still active *today*. Copying that verbatim for `is_current`
would be wrong: `member_remove` (`app/routers/parcels.py`) ends a tenancy
by setting `assigned_until = date.today()`, and the existing regression
test `tests/test_members_signin_sheet.py::test_signin_sheet_excludes_former_residents`
already locks in that this tenant must be excluded *the same day*, not
starting the next day. So `is_current` uses `assigned_until is None or
assigned_until > date.today()` -- strict greater-than. Two boundary tests
guard both directions: the existing same-day-exclusion test above, and a
new same-file test asserting a `assigned_until` of tomorrow still counts
as current.

**`is_invoice_address` is not affected, on purpose.** The
`ck_invoice_address_only_for_current_tenants` CHECK constraint (see
[ADR 0035](./0035-invoice-address-flag-on-member-parcel-assignments.md))
already rejects `is_invoice_address = True` for *any* `assigned_until`,
past or future -- a stricter, separate rule protecting against invoices
being mailed to someone mid-move-out, not just someone already gone. The
three call sites that pair an `assigned_until IS NULL` check with
`is_invoice_address` (`app/invoice_generation.py`,
`app/invoice_delivery.py`) are deliberately left untouched: swapping them
to `is_current`/`current_tenant_filter()` would be a no-op in practice
(a future-dated row can never be an invoice address anyway) but reads as
loosening invoicing eligibility, which this issue does not ask for.

**Mutation sites are out of scope.** This is a read-side fix only.
`member_remove`, `member_assignment_update`, and `member_assign`'s
reactivation branch (all in `app/routers/parcels.py`) still just read and
write `assigned_until` directly -- no new "schedule a termination" UI
flow was added. No Alembic migration either: no new column, purely a
computed property and a query filter over the existing one.
