# Task board: multiple assignees per card

**Context:** `Task.assigned_to_id` was a single nullable FK to `users.id`
(see ADR 0034/0044). GitHub issue #109 asked for exactly what the title
says: *"add possibility to add more that one users to task"* -- a club
task like "renew the insurance policy" is often actually owned jointly by
two board members, and the single-assignee field couldn't express that.

**Decision:**

1. **Replace `Task.assigned_to_id` with a `TaskAssignee` join table**,
   mirroring `GroupMembership`'s shape (own `id`, `task_id`/`user_id` FKs,
   `UniqueConstraint` on the pair) rather than a raw SQLAlchemy `secondary=`
   table -- consistent with every other m:n relationship already in this
   codebase (`GroupMembership`, `InvoiceItemDefinitionMember`,
   `InvoiceItemTemplateMember`), none of which use `secondary=` either.
   Migration `0055_task_multiple_assignees` carries over each existing
   `assigned_to_id` as a single row before dropping the column.

2. **`task_id`/`user_id` are both `ON DELETE CASCADE`**, unlike the old
   `assigned_to_id`'s `ON DELETE SET NULL` -- deleting a task should drop
   its own assignment rows (nothing else references them), and this
   matches `group_memberships`' existing FK behavior for the `user_id`
   side. A user with real assignment history still can't be hard-deleted:
   `app/routers/admin.py`'s `_USER_REFERENCE_CHECKS` now checks
   `TaskAssignee.user_id` instead of the old `Task.assigned_to_id`, same
   gate as before, just pointed at the new table.

3. **`Task.assigned_to_ids` is a Python `@property`** (`[a.user_id for a
   in self.assignees]`), not a second mapped column, so the REST API's
   `KanbanTaskOut.assigned_to_ids` can read it directly via
   `from_attributes=True` the same way it read the old scalar column.
   Every route returning `KanbanTaskOut` (or resyncing assignees) eager-
   loads `Task.assignees` first and refreshes only the specific attributes
   touched after a write (`attribute_names=[...]`) rather than a blanket
   `db.refresh()` -- a blanket refresh would expire the already-loaded
   `assignees` collection and risk an unawaited lazy-load under
   `asyncpg` (see CLAUDE.md's identity-map sharp edge; this exact shape
   of bug has hit this codebase more than once).

4. **Update semantics: full resync, not incremental add/remove.** Both
   the web form and `PUT /api/v1/tasks/{id}` treat `assigned_to_ids` as
   "the complete set of assignees after this request" -- existing
   `TaskAssignee` rows not in the submitted list are deleted, new ones are
   added. This mirrors `finances.py`'s existing `parcel_scopes`/
   `member_scopes` resync-on-update pattern exactly (delete what's no
   longer there, then re-add the submission). `KanbanTaskUpdate.
   assigned_to_ids` stays `Optional[List[str]] = None` so omitting the
   field entirely from a `PUT` body leaves existing assignees untouched,
   consistent with every other partial-update field on that schema
   (`exclude_unset=True`).

5. **Web form: a checkbox grid over `active_users`, not a `<select
   multiple>`.** A native multi-select is harder to operate with a mouse
   (ctrl/cmd-click to multi-select isn't discoverable) and doesn't show
   which options are already checked as clearly as checkboxes.
   `InvoiceItemDefinition`'s member/parcel picker already established a
   checkbox-grid pattern (`app/templates/finances/run_detail.html`'s
   `scope_picker` macro) for a similar "select some subset of a small
   entity list" UI; this reuses the same visual shape but not the macro
   itself, since that macro also wires up an all-or-none toggle switch
   and a searchable filter that a task's assignee list -- realistically a
   handful of board/admin users -- doesn't need.

6. **`tasks.form.unassigned_option`'s translation key was removed** (all
   7 locales) rather than kept dangling -- an unchecked checkbox grid
   already reads as "nobody assigned," so there's no longer a distinct
   "Unassigned" option to render.

**Consequence:** `docs/module-tasks.md`'s "Test-DB sharp edge" note about
manually seeding `TaskList` rows in tests still applies unchanged; tests
creating a `Task` with assignees additionally need real `User` rows to
reference, same as the existing `test_web_board_renders_and_create_edit_delete_flow`
test already did for the single-assignee case.
