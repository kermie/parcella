# Task board module

A general-purpose kanban board (To Do / In Progress / Done) for club
business that isn't tied to a work session -- "renew the insurance
policy," "call the electrician," "follow up with the roofer." Admin/board
only, both for viewing and editing.

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
tasks  -- one row per kanban card
```

**`Task`** has `status` (`TaskStatus`: `TODO`/`IN_PROGRESS`/`DONE`,
fixed for v1 -- no per-club column configuration) and `position` (a
gapless 0-based index within its column, used for both drag-and-drop
ordering and the CSV/API iteration order). `assigned_to_id` and
`created_by_id` both reference `User` (not `Member`) -- consistent with
this being an internal admin/board tool, not member-facing club
business (compare `ChangeHistory.changed_by_id`, `WorkSession.created_by_id`).

## Card ordering: `app/task_board.py`

Shared between the web router and the REST API so both move cards with
identical semantics:

- `next_position()`: a new card is appended to the end of its column.
- `move_task()`: moves a card to a column + index. Renumbers the
  affected column(s) in one pass -- correct whether it's a cross-column
  move or a pure same-column reorder, since a same-column move is just
  "exclude this card, reinsert at the new index, renumber."
- `close_gap_after_delete()`: renumbers the remaining cards in a column
  after one is deleted, so `position` never has holes.

All three fully rewrite the affected column's `position` values rather
than doing fractional/gap-based positioning -- correct and simple at
the card counts a club's task board will realistically ever have.

## Web UI: drag-and-drop

`app/templates/tasks/board.html` implements native HTML5 drag-and-drop
(no library) -- dragging a card shows an insertion point among the
target column's other cards (via comparing the pointer position against
each card's bounding rect, the standard vanilla-JS kanban technique),
and dropping calls `POST /tasks/{id}/move` with the resulting column +
index as JSON. On any request failure, the page reloads to resync with
the server rather than leaving the UI in a state the backend disagrees
with.

Create/edit use the same separate-page pattern as the rest of the app
(`/tasks/new`, `/tasks/{id}/edit`) rather than a modal, for consistency
with Members/Parcels/Work Hours.

## A full REST API, alongside the web UI

`/api/v1/tasks` covers list (with an optional `status` filter),
retrieve, create, update, delete, and a dedicated `POST .../move`
endpoint that runs the same `move_task()` logic the web UI's
drag-and-drop uses. Admin/board only (`require_admin_api`), matching
the web UI's permission level -- unlike most modules, viewing is not
open to regular members here (see the ADR entry for why).

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

9 tests covering: default placement in TODO at the end of the column,
cross-column moves (and that the old column compacts correctly),
same-column reordering, delete closing the gap in the remaining column,
field updates, the admin/board-only permission boundary on both the web
UI and the API (403 for a readonly member), the full web create/edit/
move/delete flow, and the module-disabled-returns-404 case. 123/123
across the whole suite, all passing against real PostgreSQL.
