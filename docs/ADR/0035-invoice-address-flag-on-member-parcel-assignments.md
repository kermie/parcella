# Invoice-address flag on member-parcel assignments

**Context:** A parcel can have several assigned members (couples,
families, community gardens), and each member has their own snail-mail
address (`street`/`postal_code`/`city` fields on `Member` -- there's no
separate `Address` table). Since these addresses can genuinely differ,
one of them needs to be picked as the parcel's invoice address.
Migration `0035_add_invoice_address` adds `is_invoice_address` to
`member_parcels`.

**Same shape as the removed `is_primary_tenant`, different concept.**
`member_parcels` previously had an `is_primary_tenant` role distinction,
removed in migration `0022_remove_tenant_role` (see
[ADR 0018](./0018-removed-the-primary-co-tenant-role-distinction.md))
because the board rejected ranking residents for *liability* -- everyone
assigned to a parcel is held jointly responsible regardless of who's
marked primary. `is_invoice_address` reuses the exact same shape (a
plain boolean on the assignment row, default `True`, no DB-level "only
one true per parcel" constraint), but it answers a different question:
not "who's responsible," but "which of these addresses does the invoice
get mailed to." That's a data-selection concern, so re-adding a
per-assignment flag for it doesn't reopen the liability decision ADR
0018 settled.

**Couples/households: resolve at document-generation time, not with more
flags.** There is no invoice-generation feature yet -- this migration
only adds the flag and the assignment-form UI to set it. But when that
feature is built: two residents of a parcel (e.g. a married or
unmarried couple) sharing one address may both need to appear on the
invoice letter. The recommendation is to resolve that the same way
`household_grouping()` in `app/insurance_utils.py` already resolves the
equivalent problem for insurance, rather than adding a second boolean or
letting several rows be flagged as canonically "the" invoice address: at
print/send time, look up other current residents who share the exact
address of the member flagged `is_invoice_address`, and list all of
them on the letter. The flag itself should only ever need to point at
one address record per parcel; who else gets named alongside it is
computed, not stored.

**Unlike `is_primary_tenant`, this flag *is* DB-constrained: a former
tenant can never hold it.** Migration `0036_invoice_current_only` adds
`CHECK (NOT is_invoice_address OR assigned_until IS NULL)` on
`member_parcels`. Reasoning: `is_primary_tenant` was purely descriptive
(a UI label with no downstream consequence once the tenancy ended), so
it never needed this kind of guard. `is_invoice_address` drives where an
actual annual invoice gets mailed -- silently leaving it `true` on a row
whose tenancy has since ended would mean invoices keep going to someone
who's moved out. So every code path that sets `assigned_until` (ending a
tenancy via `member_remove`, correcting the period via
`member_assignment_update`, or creating a historical assignment via the
API) also forces `is_invoice_address` to `False` in the same write, and
the CHECK constraint is the backstop against any path that doesn't.
