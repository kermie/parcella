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
families). The assignment table `member_parcels` additionally carries
`is_primary_tenant` (bool) and `assigned_from`/`assigned_until` (date fields).

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

## Known pitfalls

- `row.get("Column", "")` does NOT protect against `None` values when a
  CSV row has fewer fields than the header row (Python fills those with
  `None`; the default only kicks in when the key is missing entirely).
  Always use `(row.get("Column") or "")`.
- `scalar_one_or_none()` raises an error as soon as more than one row comes
  back -- for duplicate *detection* (where multiple matches can be
  expected), `.scalars().first()` is the right choice.
