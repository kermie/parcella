# Task board sort: client-side and purely visual, not a position rewrite

**Context:** GitHub issue #120 asked to sort tasks by due date (grouped
by month/year, "like we have it in the date picker" -- referring to the
due-month filter dropdown from issue #119/[ADR 0049](./0049-task-board-search-and-filter-client-side.md))
and by priority.

**Decision:**

1. **Sort is client-side JS reordering DOM nodes, exactly like search/
   filter (ADR 0049) -- no new server-side data.** Both sort keys
   (`data-priority`, `data-due-month`) were already rendered on each
   card for the filter feature, so `board()` needed zero changes.

2. **Sorting never rewrites `Task.position`.** `position` remains
   "manual drag order" and is the only thing actually persisted --
   choosing "Due date" or "Priority" from the sort dropdown just
   re-appends each column's card elements in the new order in the DOM.
   Switching back to "Manual" restores the exact original order
   captured once at page load (`originalOrderByList` in
   `board.html`), not a re-fetch. This avoids a much larger, riskier
   feature (a second persisted ordering scheme alongside `position`,
   or silently overwriting drag order every time a sort is applied)
   that wasn't asked for -- "sort my tasks" reads as a view preference,
   not "permanently reorder my board."

3. **Due-date sort compares `data-due-month` (a `YYYY-MM` string), not
   the exact day.** Per the issue's explicit callback to the due-month
   filter's "MMMM YYYY" granularity: two tasks due in the same month
   are not distinguished by day, only by their original (manual)
   relative order, since the sort is a stable sort over each column's
   captured original array. Undated tasks sort last regardless of
   direction (`data-due-month || '9999-99'`, a value that always
   string-compares after any real `YYYY-MM`).

4. **Priority sort order is High -> Medium -> Low -> (none last)** --
   the natural "most urgent first" reading of `TaskPriority`, not
   enum declaration order or alphabetical.

5. **Card dragging is disabled while any non-manual sort is active**,
   same reasoning as ADR 0049 point 4 for filters: a sorted view's DOM
   order doesn't match `position`, so "drop here" would have no
   meaningful target index to persist. Selecting "Manual" re-enables
   dragging immediately. Filter-active and sort-active are independent
   flags (`filtersActive`/`sortActive`) that both gate the same
   `dragstart` guard -- either one alone is enough to disable dragging.
