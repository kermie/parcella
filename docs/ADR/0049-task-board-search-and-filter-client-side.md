# Task board search and filter: client-side, not a server round trip

**Context:** GitHub issue #119 asked for search (title, description,
tags, comments) and filters (due month/year, priority, tags, owner) on
the task board.

**Decision:**

1. **Filtering happens entirely in the browser**, over `data-*`
   attributes already rendered on each `.kanban-card`
   (`data-search-text`, `data-priority`, `data-tags`,
   `data-assignee-ids`, `data-due-month`), not a server-side query with
   re-rendered HTML. The board is a single page showing every list at
   once (unlike every other module's paginated/table views), and this
   codebase already has a precedent for exactly this shape of feature:
   admin settings' card search (issue #71,
   `app/templates/admin/settings.html`) filters/highlights entirely
   client-side for the same reason -- everything's already on the page,
   so there's nothing a round trip would add except latency and a
   flash of re-rendered content.

2. **`data-search-text` folds in comment content, which the board didn't
   previously load at all.** Comments used to live only on the task
   edit page (`docs/module-tasks.md`'s "Comments" section) -- `board()`
   now also `selectinload(Task.comments)` so their text can be searched
   without a per-card fetch. This is the one server-side change the
   feature needed; everything else (priority, tags, assignees, due
   date) was already being rendered on the card in some form.

3. **Filter dropdown options (tag, owner) are computed from what's
   actually on the board**, not every tag/user that's ever existed --
   `board()` builds `tag_options`/`assignee_options` by scanning the
   already-loaded tasks rather than querying `User`/some tag registry
   independently (there is no separate tag entity, see
   `docs/module-tasks.md`'s tags section). An option nothing currently
   matches would be actively misleading in a filter UI.

4. **Card dragging is disabled while any filter is active, rather than
   trying to keep it correct.** `position` is a dense 0-based index
   over *all* cards in a list (`app/task_board.py`); with some cards
   hidden by a filter, "drop this card here, before that visible one"
   has no single correct absolute position among the full sibling set
   including the hidden ones. Rather than reverse-engineer a correct
   index from a partial view (fragile, and silently wrong if gotten
   slightly off), the existing `dragstart` handler just bails out
   (`event.preventDefault()`) whenever a search term or filter value is
   set. Column (list) drag/reorder is untouched -- it never depends on
   individual cards, so it stays available even while a filter narrows
   the board down.
