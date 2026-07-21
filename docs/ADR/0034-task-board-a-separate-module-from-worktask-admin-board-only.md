# Task board: a separate module from WorkTask, admin/board only

**Context:** the work-hours module already has a `WorkTask` model for
tracking session-scoped work (backlog -> scheduled to a session ->
assigned to a signed-up participant, see `docs/module-work-hours.md`).
A request came in for a general kanban board for club business that
isn't tied to any work session -- "renew the insurance policy," "call
the electrician."

**Decision, confirmed with the person requesting the feature before
building it:**

1. **Fully separate module, not an extension of `WorkTask`.** Stretching
   `WorkTask` to cover both cases would mean its session/participant
   fields growing more optional and its status semantics diverging
   depending on which use case a given row was for. A new `Task` model,
   table, and pair of routers is simpler than a shared model trying to
   serve two different shapes of "task."
2. **Admin/board only, for both viewing and editing** -- unlike most
   modules (Inventory, the community calendar), where viewing is open
   to any logged-in member. This is internal club-business tracking
   ("who's following up on the insurance renewal"), not something every
   member needs visibility into. Reflected as `require_admin`/
   `require_admin_api` on every route, not just the mutating ones.
3. **Fixed three-column workflow (`TODO`/`IN_PROGRESS`/`DONE`), no
   per-club column configuration in v1** -- unlike Inventory's
   freely-configurable categories. A fixed workflow covers the actual
   request; configurable columns are a bigger feature (reordering,
   renaming, migrating existing cards when a column is removed) that
   wasn't asked for.
4. **`modul_tasks` defaults to `True`** -- unlike `cloud_storage`,
   `announcements`, and `public_signup_api`, this module doesn't store
   outbound credentials or open a public/unauthenticated endpoint, so
   there's no security-relevant reason to require an opt-in.

**Consequence:** `assigned_to_id`/`created_by_id` on `Task` reference
`User`, not `Member` -- consistent with this being an admin/board tool
(compare `WorkSession.created_by_id`), not member-facing club business.

See `docs/module-tasks.md` for the full module writeup, including a
schema-class naming collision (`TaskCreate`/`TaskOut`/etc. already
existed for `WorkTask`) found and fixed while building this.
