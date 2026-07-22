# Module: Members & Parcels (Core)

The core module -- always active, cannot be disabled (unlike the optional
modules such as Work Hours or Water/Electricity).

## Data model

```
members               – club members (core data)
member_phones          – n phone numbers per member
member_emails          – n email addresses per member
parcels                – garden parcels
member_parcels          – m:n member <-> parcel assignment
change_history          – generic audit log (see below)
```

## Key decisions

**m:n assignment from the start.** A member can have multiple parcels
(multiple gardens), and a parcel can have multiple members (couples,
families). The assignment table `member_parcels` carries
`assigned_from`/`assigned_until` (date fields) for tenancy history.
Originally also had an `is_primary_tenant` role distinction; removed
(see [Architecture Decisions](./ADR/0018-removed-the-primary-co-tenant-role-distinction.md)) since the
board holds every resident of a parcel jointly responsible, with no
hierarchy between them.

**`is_invoice_address` on `member_parcels`.** Residents of a parcel can
have different snail-mail addresses (each address lives on `Member`
itself -- there's no separate `Address` table); this flag marks which
assigned member's address is used to send that parcel's invoices. Same
shape as the removed `is_primary_tenant` (a plain boolean on the
assignment row, defaulting to `True`, no "exactly one per parcel"
constraint) but a different concern: it selects an address for postal
mail, not a liability rank -- see
[Architecture Decisions](./ADR/0035-invoice-address-flag-on-member-parcel-assignments.md).
A former tenant can never hold this flag -- a CHECK constraint
(`ck_invoice_address_only_for_current_tenants`, migration
`0036_invoice_current_only`) enforces `NOT is_invoice_address OR
assigned_until IS NULL`, and every code path that ends a tenancy clears
the flag in the same write so invoices don't keep going to someone
who's moved out.
Households (e.g. a couple who should both appear on the invoice letter)
are resolved at document-generation time by matching addresses among
current residents, the same way `household_grouping()` in
`app/insurance_utils.py` already does for insurance -- not by adding
more flags to the assignment row.

**Tenancy history instead of deletion.** When a tenancy ends,
`assigned_until` is set instead of deleting the row. This keeps it
traceable who held which parcel when -- important for questions that come
up years later. If a member later takes on the same parcel again, the
existing (ended) assignment is reactivated instead of creating a second
row (there is a `UniqueConstraint` on `member_id, parcel_id`).

**Active vs. inactive members.** A member counts as active if
`deleted_at IS NULL` and (`member_until IS NULL` or `member_until` is in
the future). The central helper function `active_member_filter()` in
`app/database.py` encapsulates this -- used everywhere only active members
are relevant (dropdowns, reports, assignments). The member list itself
shows only active members by default, with an "Show inactive" checkbox
for the history (e.g. deceased members).

**Change history (ChangeHistory).** A generic audit log
(`app/change_tracker.py`) that logs field changes on arbitrary entities
(currently used for parcels: area, status, plot number, etc.). Instead of
building a separate history table for every table, there is one shared
`change_history` table with `entity_type`, `entity_id`, `field_name`,
`old_value`, `new_value`. Usage:

```python
tracker = ChangeTracker(parcel, "Parcel", ["plot_number", "area_sqm", "status"])
# ... change fields ...
await tracker.commit(db, user.id)
```

**CSV import with automatic delimiter detection.** An early version hard-
coded a semicolon as the delimiter -- that broke as soon as someone opened
the export file in Excel and saved it again (Excel switches to a comma
depending on locale settings). It now uses `csv.Sniffer()` to detect the
delimiter, with semicolon as the fallback.

## General-meeting sign-in sheet

`/members/signin-sheet` generates a PDF (`app/meeting_signin_sheet.py`,
WeasyPrint): current residents, grouped by parcel number, one
signature line each -- for printing and bringing to a physical
members' meeting.

**Not gated by a module flag, and permission-checked the same as the
member list itself (`require_user`).** It's just another view onto
member data that's already visible to anyone who can see the member
list, not a separate feature area with its own security surface --
adding a module flag here would be ceremony without a real decision
behind it.

**Deliberately not constrained to one page**, unlike the announcement
flyer (`app/print_publisher.py`): a real roster can run to several
pages, and there's no "shorten it" option for a list of people who need
to physically sign something. It's a normal multi-page document with a
repeating header/footer (same `@top-center`/`@bottom-center` running-
element technique as the flyer) and "Page X of Y" numbering via
`counter(page)`/`counter(pages)`.

**The headline is a plain editable text field, not a template with
placeholders.** The original ask included an example like "General
meeting on {date}" -- that's illustrative phrasing, not a literal
`{date}` token to substitute. The form pre-fills a sensible default
(today's date) into an ordinary text input; the admin can edit it to
say anything before generating.

**Parcels with multiple current residents get one row per person,
sharing a single rowspan'd parcel-number cell.** Reads like a real
paper sign-in sheet: the parcel number appears once per group, but
every co-tenant still gets their own name and their own signature
line.

**`app/pdf_utils.py` was factored out of `app/print_publisher.py`**
once this became the second PDF generator embedding local images as
base64 data URIs (the club logo, in both cases) -- shared to avoid a
second copy of the same small helper, not because either module
depends on the other.

## Known pitfalls

- `row.get("Column", "")` does NOT protect against `None` values when a
  CSV row has fewer fields than the header row (Python fills those with
  `None`; the default only kicks in when the key is missing entirely).
  Always use `(row.get("Column") or "")`.
- `scalar_one_or_none()` raises an error as soon as more than one row comes
  back -- for duplicate *detection* (where multiple matches can be
  expected), `.scalars().first()` is the right choice.
