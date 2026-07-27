# Task board: configurable lists replace the fixed status enum

**Context:** [ADR 0034](./0034-task-board-a-separate-module-from-worktask-admin-board-only.md)
shipped the task board with a fixed three-column workflow
(`TODO`/`IN_PROGRESS`/`DONE`) and explicitly deferred configurable
columns: *"a bigger feature (reordering, renaming, migrating existing
cards when a column is removed) that wasn't asked for."* GitHub issue
#100, filed by a board/counsel member, asked for exactly that: *"let me
enhance the task board with more lists."*

**Decision:**

1. **Replace the `TaskStatus` enum column with a `TaskList` table.**
   `Task.status` becomes `Task.list_id` (FK). A board/admin user can
   create, rename, reorder, and delete lists from the board UI and the
   REST API. Migration `0054_task_lists` seeds the three original
   values ("To Do"/"In Progress"/"Done") as the default lists, so an
   existing board looks unchanged immediately after upgrading -- see
   `docs/module-tasks.md` for the full schema/migration writeup.

2. **A board must always have at least one list; deleting a non-empty
   list requires an explicit destination.** Two alternatives were
   rejected:
   - *Cascade-delete the list's cards* -- this repo historizes rather
     than deletes (ADR 0005); silently losing cards because their
     column was removed would be exactly the kind of surprising data
     loss that convention exists to avoid.
   - *`ON DELETE SET NULL`, like `InventoryCategory`* -- doesn't fit a
     kanban board: an inventory item can meaningfully fall back to
     "uncategorized," but a kanban card has no equivalent "no column"
     state to render. Every card must always be in a visible list.

   So `delete_list()` (`app/task_board.py`) requires a `move_to_list_id`
   whenever the list being deleted still has cards, and refuses to
   delete the last remaining list outright. The web UI mirrors the
   "last list" guard client-side (delete disabled in the dropdown) as a
   convenience, not a substitute for the backend check.

3. **Breaking REST API change, accepted deliberately.** `status` on
   `/api/v1/tasks*` becomes `list_id`. There's no versioning scheme in
   this API that would let the old shape coexist with the new one, and
   once lists are user-renameable/addable, "status" has no stable
   meaning left to preserve -- keeping it as a parallel/derived field
   would only work for the three original default lists and silently
   break for anything else, which is worse than a clean, documented
   break.

4. **List names are free text, not i18n keys -- column labels stop
   being translated per viewer.** The old `column_todo`/
   `column_in_progress`/`column_done` i18n keys are removed. This
   follows the precedent already set by `InventoryCategory.name`: once
   an admin can add/rename columns, there's no way to keep translating
   them per viewer, so the name is stored once, in whatever language the
   admin used. The migration seeds the defaults in English; a club that
   wants a different language renames them once, same as renaming an
   Inventory category.

5. **List reordering has no direct precedent in this codebase to
   copy.** `InventoryCategory`, the closest existing "user-managed named
   collection," has no ordering at all (categories list alphabetically).
   Column drag-and-drop reuses the same insertion-point technique
   already used for card drag-and-drop in `board.html`, kept in
   separate JS state so the two drag gestures don't interfere, and
   `move_list()`/`next_list_position()` in `app/task_board.py` mirror
   the existing `move_task()`/`next_position()` shape at the list level.

**Consequence:** the test suite builds its schema via
`Base.metadata.create_all` rather than running Alembic migrations (see
`tests/conftest.py`), so migration `0054`'s seeded default lists never
exist in tests -- any test creating a `Task` has to seed its own
`TaskList` rows first. See `docs/module-tasks.md`'s "Test-DB sharp edge"
section.
