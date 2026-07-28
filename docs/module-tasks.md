# Task board module

A general-purpose kanban board for club business that isn't tied to a
work session -- "renew the insurance policy," "call the electrician,"
"follow up with the roofer." Admin/board only, both for viewing and
editing. Columns ("lists") are user-configurable: a board/admin user can
add, rename, reorder, and delete them (issue #100).

## Why a separate module from WorkTask

The work-hours module already has a `WorkTask` model (see
`docs/module-work-hours.md`), but it's deliberately scoped to a single
work session: a task there is either in the backlog, scheduled to a
session, or assigned to one of that session's signed-up participants.
That's a fundamentally different shape from a general club task --
there's no session to schedule against, no participant to assign to,
and no reason a card should ever need to "belong" to a work session.

Rather than stretch `WorkTask` to cover both use cases (nullable session
fields growing more nullable, status semantics diverging), this is a
fully separate model, table, and pair of routers. Confirmed with the
person requesting the feature before building it -- see
[Architecture Decisions](./ADR/0034-task-board-a-separate-module-from-worktask-admin-board-only.md).

## Data model

```
task_lists  -- one row per kanban column
tasks       -- one row per kanban card
```

**`TaskList`** (issue #100, [ADR 0044](./ADR/0044-task-board-configurable-lists.md))
is a column: `name` (free text -- see "Column labels are no longer
translated" below) and `position` (a gapless 0-based index among all
lists on the board). Originally (v1, [ADR 0034](./ADR/0034-task-board-a-separate-module-from-worktask-admin-board-only.md))
columns were a fixed `TaskStatus` enum (`TODO`/`IN_PROGRESS`/`DONE`);
migration `0054_task_lists` replaced it with this table and seeded
those same three as the default lists, so existing boards look
unchanged right after the upgrade.

**`Task`** has `list_id` (FK to `TaskList`, replacing the old `status`
enum column) and `position` (a gapless 0-based index within its list,
used for both drag-and-drop ordering and the CSV/API iteration order).
`assigned_to_id` and `created_by_id` both reference `User` (not
`Member`) -- consistent with this being an internal admin/board tool,
not member-facing club business (compare `ChangeHistory.changed_by_id`,
`WorkSession.created_by_id`).

`priority` (issue #106, migration `0055_task_priority`) is an optional
`TaskPriority` enum (`LOW`/`MEDIUM`/`HIGH`). Nullable with no default --
unset means "no priority" rather than implicitly `MEDIUM`, since every
card that existed before this migration has no real basis for a guessed
priority. Shown on the board as a small colored badge on the card
(`app/templates/tasks/board.html`) and editable from the same
create/edit form as due date and assignee; there's no separate
"priority" column or swimlane, and cards aren't auto-sorted by
priority -- `position` (drag-and-drop order) still governs ordering
within a list.

**Migration gotcha, the mirror image of the one in
[docs/module-work-hours.md](./module-work-hours.md#known-pitfalls):**
inside `op.create_table(...)`, an inline `sa.Enum(...)` column
auto-creates the Postgres type as a side effect -- but a standalone
`op.add_column(...)` on an *existing* table does not. `0055_task_priority`
needs an explicit `op.execute("CREATE TYPE taskpriority AS ENUM (...)")`
before the `add_column` call (with `create_type=False` on the column's
`sa.Enum(...)` to stop SQLAlchemy from trying a second, redundant
`CREATE TYPE`), same pattern as `0008_zaehlerwesen`'s `medium` column.
Skipping it fails at `alembic upgrade head` with `type "taskpriority"
does not exist` -- caught before merge because `run_tests.sh` runs
migrations against the disposable web image entrypoint, not just
`create_all` in the test suite itself.

## Card and list ordering: `app/task_board.py`

Shared between the web router and the REST API so both move cards/lists
with identical semantics:

- `next_position()` / `next_list_position()`: a new card/list is
  appended to the end of its list/the board.
- `move_task()`: moves a card to a list + index. Renumbers the affected
  list(s) in one pass -- correct whether it's a cross-list move or a
  pure same-list reorder, since a same-list move is just "exclude this
  card, reinsert at the new index, renumber."
- `move_list()`: same reinsert-and-renumber shape as `move_task()`, for
  reordering the columns themselves (there's only one board, so no
  cross-container case).
- `close_gap_after_delete()`: renumbers the remaining cards in a list
  after one is deleted, so `position` never has holes.
- `delete_list()`: the one genuinely new piece of logic -- see "List
  management" below.

All of these fully rewrite the affected list's `position` values rather
than doing fractional/gap-based positioning -- correct and simple at
the card/list counts a club's task board will realistically ever have.

## List management

A board must always have at least one list. `delete_list()` enforces
two rules, each raising a `ValueError` with a short code
(`last_list`/`missing_target`/`target_not_found`) that the routers turn
into a translated 400 (web) or a plain-English 400 (API):

- The last remaining list on a board cannot be deleted.
- Deleting a list that still has cards requires a `move_to_list_id`
  identifying a *different*, existing list -- its cards are appended (in
  their existing relative order) to the end of that list, which is then
  renumbered. Chosen over silently cascade-deleting the cards (this
  repo historizes rather than deletes, see ADR 0005) and over an
  Inventory-style `ON DELETE SET NULL` (which doesn't fit here: a card
  must always be in a visible column, unlike an inventory item, which
  can fall back to "uncategorized").

The web UI enforces the same "last list" rule client-side too (the
delete action is disabled in the dropdown when only one list remains) --
belt and braces, not a substitute for the backend check.

## Column labels are no longer translated

Before configurable lists, `column_todo`/`column_in_progress`/
`column_done` were i18n keys, translated per viewer language. Now that
an admin can add or rename lists, a list's `name` is free text stored
once per club -- like `InventoryCategory.name` -- not re-translated per
viewer. The migration seeds the three default lists in English; a club
that wants them in another language renames them once, the same way
they'd rename an Inventory category.

## Web UI: drag-and-drop

`app/templates/tasks/board.html` implements native HTML5 drag-and-drop
(no library) for both cards and columns, using the same insertion-point
technique for each (comparing the pointer position against the target's
siblings' bounding rects) but kept in separate JS state (`draggedCard`
vs. `draggedColumn`) so the two gestures never interfere:

- Dragging a **card** shows an insertion point among the target list's
  other cards and calls `POST /tasks/{id}/move` with the resulting
  `list_id` + index as JSON.
- Dragging a **column header** reorders the columns and calls
  `POST /tasks/lists/{id}/move` with the resulting index.

On any request failure, the page reloads to resync with the server
rather than leaving the UI in a state the backend disagrees with.
Rename/delete are Bootstrap modals (one shared modal per action,
populated from the clicked column's `data-*` attributes), following the
same `data-bs-toggle="modal"` pattern as the CSV-import modal in
`app/templates/members/list.html`, rather than one modal per column.

Create/edit (of a card) use the same separate-page pattern as the rest
of the app (`/tasks/new`, `/tasks/{id}/edit`) rather than a modal, for
consistency with Members/Parcels/Work Hours; lists themselves are
managed inline on the board instead, since there's no other data to
edit on a list besides its name.

## A full REST API, alongside the web UI

`/api/v1/tasks` covers list (with an optional `list_id` filter),
retrieve, create, update, delete, and a dedicated `POST .../move`
endpoint that runs the same `move_task()` logic the web UI's
drag-and-drop uses. `/api/v1/tasks/lists` (registered before the
`/{task_id}` routes so a literal path like `/lists` is never captured by
a `{task_id}` path parameter) covers the equivalent CRUD + move for
columns, plus `DELETE .../lists/{id}?move_to_list_id=...` for the
reassign-then-delete flow above. Admin/board only (`require_admin_api`),
matching the web UI's permission level -- unlike most modules, viewing
is not open to regular members here (see the ADR entry for why).

**Breaking change (issue #100):** `status` (`TODO`/`IN_PROGRESS`/`DONE`)
on the task endpoints was replaced by `list_id`. There's no versioning
scheme in this API to preserve the old shape alongside the new one, and
once lists are user-renameable/addable, "status" has no stable meaning
left to preserve.

## A naming collision found while building this

`app/schemas.py` already defined `TaskBase`/`TaskCreate`/`TaskUpdate`/
`TaskOut` for `WorkTask`. Python doesn't error on a duplicate class
name in the same module -- it silently lets the later definition shadow
the earlier one. This first went unnoticed (both sets of names looked
individually correct in isolation) and broke `api_work_hours.py`'s task
endpoints at runtime, caught by the existing
`test_task_lifecycle` test in `tests/test_work_hours.py`. Fixed by
prefixing this module's schemas `KanbanTask*` instead, with a comment
in `app/schemas.py` explaining why -- worth checking for whenever a new
module's domain noun (here, "task") is generic enough to already be in
use elsewhere.

## Testing

`tests/test_tasks.py` covers: default placement in the first list at the
end, cross-list moves (and that the old list compacts correctly),
same-list reordering, delete closing the gap in the remaining list,
field updates, list create/rename/reorder, deleting an empty list,
deleting a list with cards (reassignment, order preserved, target
renumbered), the "can't delete the last list" and
"non-empty delete needs a target" 400s, the admin/board-only permission
boundary on both the web UI and the API (403 for a readonly member, on
both task and list endpoints), the full web create/edit/move/delete flow
for both cards and lists, the module-disabled-returns-404 case, and
setting/updating/clearing `priority` via both the API and the web
form (including that the priority badge disappears from the rendered
board once cleared).

**Test-DB sharp edge:** the test suite builds its schema from
`app/models.py` via `Base.metadata.create_all` (see `tests/conftest.py`),
not by running Alembic migrations -- so migration `0054`'s seeded
"To Do"/"In Progress"/"Done" rows never exist in tests. Any test that
creates a `Task` needs to seed its own `TaskList` rows first (see
`_seed_lists()` in `tests/test_tasks.py` and `_seed_task_lists()` in
`tests/test_sample_data.py`, both of which mirror the migration's seed
data). `app/sample_data.py`'s `_seed_tasks()` looks its three default
lists up by name for the same reason -- it runs after migrations in
production, but tests have to seed them manually first.
