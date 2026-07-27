# Invoice item targeting: plot-scoped vs. person-scoped, as two independent mechanisms

**Context:** Annual invoicing (issue #55 onward) started out entirely
parcel-shaped: every `InvoiceItemDefinition` billed onto occupied
parcels, scoped via `applies_to_all_parcels`/`parcel_scopes`.
`FIXED_PER_PERSON` (e.g. annual dues, honorary membership) was forced
through the same machinery -- a flat fee per resident, counted via
`residents_count` on a parcel. That broke for members with no current
parcel (an honorary or supporting member with no plot literally
couldn't be billed), so a first fix added `Invoice.member_id` (nullable,
alongside the existing nullable `parcel_id`, with
`ck_invoice_exactly_one_subject` enforcing exactly one is set) plus a
bolt-on `applies_to_members_without_parcel` flag layered on top of the
parcel scoping fields.

The user rejected that shape: a form offering "also bill members
without a current parcel" *and* still showing a parcel picker to select
specific plots mixes two unrelated targeting questions -- "which
parcels" and "which people" -- into one field set. Asked to "resolve
this problem in a manner of a clean data structure."

**Decision: `pricing_mode` is the router between two independent,
symmetric targeting mechanisms, not one mechanism with an opt-in
escape hatch.**

- **Plot-scoped modes** (`FIXED_PER_PARCEL`, `PER_SQM`, `WATER_USAGE`,
  `ELECTRICITY_USAGE`, `INSURANCE_COST`): unchanged --
  `applies_to_all_parcels`/`parcel_scopes` (`InvoiceItemDefinitionParcel`
  / `InvoiceItemTemplateParcel`), billed one Invoice per parcel.
- **`FIXED_PER_PERSON`**: gets its own mirror-image scope --
  `applies_to_all_members`/`member_scopes`
  (`InvoiceItemDefinitionMember`/`InvoiceItemTemplateMember`, same
  shape as the parcel tables: `id`, FK to the owning item row
  `ondelete=CASCADE`, FK to `members.id` `ondelete=CASCADE`, unique
  constraint on the pair) -- billed one Invoice per targeted member via
  `Invoice.member_id`, regardless of whether that member currently has
  a parcel. `applies_to_members_without_parcel` is retired outright,
  fully superseded by direct member selection: a form for a
  `FIXED_PER_PERSON` item shows a member picker; a form for any other
  mode shows a parcel picker. Never both, never neither.
- Quantity for `FIXED_PER_PERSON` is now always 1 -- the old "count
  residents of this parcel" behavior is gone, since it was a proxy for
  "how many people does this fee apply to" that direct member selection
  answers exactly. `item_quantity_and_price()` in
  `app/invoice_generation.py` dropped its `residents_count` parameter
  and the whole `FIXED_PER_PERSON` branch; the mode is excluded from the
  parcel loop's `applicable_defs` filter entirely and handled solely by
  `_compute_member_invoices()`, which resolves targets per definition
  (`applies_to_all_members` -> `active_member_filter()`; else ->
  `member_scopes`) and aggregates every matching definition for a given
  member onto **one** invoice with multiple lines, mirroring how the
  parcel loop already aggregates multiple applicable defs onto one
  parcel invoice.

Migration `0051_item_person_scope` creates the two new tables, adds
`applies_to_all_members` (server default `true`) to both item tables,
carries over existing intent with an explicit
`UPDATE ... SET applies_to_all_members = true WHERE pricing_mode = 'FIXED_PER_PERSON' AND applies_to_members_without_parcel = true`
on both tables (not just the column default, so already-configured
`true` survives correctly), then drops the old column. Checked against
the real dev DB first: both real `FIXED_PER_PERSON` templates in use
("Mitgliedsbeitrag ohne Garten", "Ehrenmitgliedschaft") already had
`applies_to_all_parcels=false` -- i.e. were already used purely as
person-scoped fees in practice -- and the only two *finalized* invoice
items on this mode were obviously-named test data with zero real
invoices generated from them, so there was no real invoicing history to
reconcile.

**Why not keep one shared scope concept ("things this item applies
to") and just add a `subject_type` discriminator?** A parcel and a
member are different entities with no natural overlap -- there's no
query that ever needs "give me the scope rows for this item regardless
of whether they're parcels or members." Keeping them as two separate
tables/fields, routed purely by `pricing_mode`, means each side stays
exactly as simple as the plot-scoped side already was, and the UI/router
code for each is a straight structural mirror of the other rather than
a shared abstraction with a type tag threaded through it.

**Consequence for the UI:** `run_detail.html` and
`item_template_list.html` both carry two parallel toggle/picker blocks
per item row (`parcel-toggle-wrap-{id}`/`member-toggle-wrap-{id}`,
`parcel-picker-block-{id}`/`member-picker-block-{id}`), and a per-row
`sync()` keyed off the pricing-mode `<select>` shows exactly one side
based on `select.value === 'fixed_per_person'`. This is more markup per
row than a single shared block would be, but it's the direct UI
expression of "these are two unrelated questions" -- collapsing them
back into one conditionally-relabeled block would reintroduce the exact
ambiguity this ADR exists to resolve.

**Update (issues #87/#89):** `INSURANCE_COST` is no longer "unchanged"
plot-scoped as the bullet above still says -- it moved into a third
category, "automatically scoped" (alongside `WORK_HOURS_SHORTFALL`,
added after this ADR by issue #83), where `applies_to_all_parcels`/
`parcel_scopes` are stored but never read for eligibility; billing is
whichever parcels `app/insurance_utils.py`'s `calculate_insurance_cost()`
returns a nonzero total for. See `docs/module-finances.md`'s
"Automatically scoped" bullet for the current, maintained description
-- left here as a historical record of the original three-mode split
rather than rewritten, per this repo's ADR convention.
