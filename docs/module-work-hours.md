# Module: Work Hours (Pflichtstunden)

> **Note on the renaming:** the code (models, tables, URLs, API
> endpoints) has been fully converted to English:
> `Arbeitseinsatz` -> `WorkSession`, `Vereinsrolle` -> `ClubRole`,
> `Patenschaft` -> `Sponsorship`, `/pflichtstunden/` -> `/work-hours/`.
> Details and lessons learned in
> [Architecture Decisions](./architecture-decisions.md).
> This page continues to describe the domain logic, which did not
> change in the process.

Manages the annual mandatory work-hours requirement: standard and special
work sessions, sponsorships as an alternative, club roles with automatic
exemption.

Module flag: `work_hours` (see `app/module_flags.py`)

Signups made through the public signup API (see
`docs/module-public-api.md`) show up directly in this module's normal
participations table on `session_detail.html`, with status `REGISTERED`
and a `note` explaining they came from the public form (and, if the
submitted name couldn't be confidently matched, that every current
resident of the parcel was registered as a precaution -- see that doc
for why). No separate UI for them; they're real `SessionParticipation`
rows.

## Data model

```
work_hours_configuration – year-based hours/rate configuration
club_roles                – club offices (board, extended board, etc.)
member_club_roles         – member -> club role assignment (year-based)
work_sessions             – standard and special sessions
session_participations    – who attended which session, with hours
sponsorships              – area responsibilities (flat-rate credit)
work_tasks                – task backlog, optionally scheduled/assigned
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

**Tasks track a workload label, deliberately nothing about the person.**
The task backlog (`work_tasks`) has a three-stage lifecycle, each stage
optional: backlog (no session yet) -> scheduled (tied to a specific work
session) -> assigned (tied to one specific participant who signed up for
that session). Every task carries a `workload` (Light / Moderate /
Demanding) so whoever coordinates a session can hand out appropriately
matched work -- but that's the *only* thing the system tracks. There is
no field anywhere for a member's age, health, or ability, and no
automated matching of any kind; the actual pairing of task to person is
a manual judgment call made by the coordinator, who knows the people
involved. Rescheduling a task to a different session (or back to the
backlog) automatically clears any participant assignment, since an
assignment only makes sense for the session that person actually signed
up for -- enforced identically in the web UI and the REST API, with the
API additionally rejecting outright any attempt to assign a task to a
participant of a session other than the one it's currently scheduled to.

## Known pitfalls

- `SessionType` and `ParticipationStatus` had to be corrected to uppercase
  after the fact (like several other enums) -- see
  [Architecture Decisions](./architecture-decisions.md) for the full
  explanation of this recurring bug.
- `session_detail.html` crashed with a 500 error for *any* session with
  at least one participant, for a long time -- a `sort(attribute=...)`
  filter referenced the old German attribute name (`mitglied.last_name`)
  from before the identifier rename instead of `member.last_name`. Found
  and fixed while building the task-assignment feature, since assigning
  a task to a participant meant actually loading that page with real
  participants for the first time in a while.
- When adding `work_tasks` via a migration, an explicit `CREATE TYPE`
  statement before `op.create_table(...)` failed with "type already
  exists" -- `sa.Enum(...)` used directly in a column definition inside
  `create_table` already creates the Postgres enum type automatically.
  Matches the pattern already used by every other enum column in this
  project's migrations; no separate `CREATE TYPE` needed.

## REST API

This module has (added after the fact) a complete set of REST API
endpoints for this module (JWT-authenticated, see `/api/docs`). See the
README for the endpoint overview. Background: early modules were
initially built as web UI only, with the API added later -- since then
the rule is that every new module gets **both** the web UI and API
endpoints **from the start** (see Architecture Decisions). The `tasks`
endpoints followed this rule from the start when they were added.

One current limitation worth knowing if you're integrating against
`PUT /api/v1/work-hours/tasks/{id}`: `TaskUpdate.session_id` can't
distinguish "this field wasn't sent" from "this field was explicitly
set to null" -- both arrive as Python `None`. To send a task back to
the backlog via the API, send an empty string (`"session_id": ""`)
rather than JSON `null`. Proper PATCH sentinel semantics would fix this
cleanly but felt like over-engineering for what's currently a single
field with this ambiguity; revisit if it becomes a real integration
pain point.


