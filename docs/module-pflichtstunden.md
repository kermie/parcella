# Module: Work Hours (Pflichtstunden)

> **Note on the renaming:** the code (models, tables, URLs, API
> endpoints) has been fully converted to English:
> `Arbeitseinsatz` -> `WorkSession`, `Vereinsrolle` -> `ClubRole`,
> `Patenschaft` -> `Sponsorship`, `/pflichtstunden/` -> `/work-hours/`.
> Details and lessons learned in
> [Architecture Decisions](./architektur-entscheidungen.md).
> This page continues to describe the domain logic, which did not
> change in the process.

Manages the annual mandatory work-hours requirement: standard and special
work sessions, sponsorships as an alternative, club roles with automatic
exemption.

Module flag: `work_hours` (see `app/module_flags.py`)

## Data model

```
work_hours_configuration – year-based hours/rate configuration
club_roles                – club offices (board, extended board, etc.)
member_club_roles         – member -> club role assignment (year-based)
work_sessions             – standard and special sessions
session_participations    – who attended which session, with hours
sponsorships              – area responsibilities (flat-rate credit)
```

## Key decisions

**Configurable billing mode.** Some associations bill mandatory hours per
member, others per lease (parcel). Both are configurable via
`WorkHoursMode` (`PER_MEMBER` / `PER_PARCEL`) instead of being hard-coded
-- an example of "what belongs generically in the product, not just in
our association".

**Tenant groups under PER_PARCEL.** When several people lease the same
parcel, their hours are added together against the parcel's single
requirement (not counted individually per person). One tenant might
contribute 2 hours, the other 3 -- together the 5 hours are fulfilled.

**Club-role exemption applies to the whole parcel.** If a member is on the
(extended) board and is therefore exempt from mandatory hours, that
exemption applies to the **entire parcel**, not just to the exempt
person -- the reasoning: the board position "covers" the rest of the
family/co-tenants. Implemented as `any()` (at least one tenant exempt ->
whole parcel exempt), not `all()`.

**Exemption applies per calendar year, not to the exact day.** If a board
member steps down in October, the exemption still applies for the whole
year. Only the following year is subject to the normal requirement again.
The `member_club_roles` table has a `year` field instead of pure date
ranges for exactly this reason.

**Sponsorships are projects, not assignments.** Originally the UI forced
you to assign a member immediately when creating a sponsorship. That was
changed: sponsorships ("areas") can be created without a member ("not yet
assigned") -- the real-world workflow is that the association first
advertises sponsorship areas and then assigns applicants. Multiple members
can share an area by creating multiple sponsorship rows with the same
area name (autocomplete via `<datalist>`) -- each gets the full hour
credit.

**Creditable hours are pre-filled from the current configuration**, but
remain freely editable (e.g. in case a sponsorship takes more effort than
the standard requirement).

## Known pitfalls

- `SessionType` and `ParticipationStatus` had to be corrected to uppercase
  after the fact (like several other enums) -- see
  [Architecture Decisions](./architektur-entscheidungen.md) for the full
  explanation of this recurring bug.

## REST API

This module has (added after the fact) a complete set of REST API
endpoints for this module (JWT-authenticated, see `/api/docs`). See the
README for the endpoint overview. Background: early modules were
initially built as web UI only, with the API added later -- since then
the rule is that every new module gets **both** the web UI and API
endpoints **from the start** (see Architecture Decisions).
