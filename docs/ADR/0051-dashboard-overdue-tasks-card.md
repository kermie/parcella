# Dashboard: overdue tasks card

**Context:** GitHub issue #127 asked for a dashboard card counting
overdue tasks from the task board, that filters to overdue cards when
clicked.

**Decision:**

1. **"Overdue" means exactly what the board already shows in red**
   (`.kanban-card-overdue` in `app/templates/tasks/board.html`: `task.
   due_date` set and in the past) -- across every list, not just ones
   still "in progress." There's no separate completed/done flag on
   `Task` to exclude by (lists are free-text columns since ADR 0044, not
   a fixed status enum), so counting by list name would be guessing at
   a convention the data doesn't actually enforce. `startseite()` in
   `app/main.py` runs one more `count()` query
   (`Task.due_date < date.today()`), unconditionally like the other
   optional-module tiles (cheap when the module's disabled, gated on
   `module_flags.tasks` only in the template) -- following the existing
   pattern from [ADR 0019](./0019-dashboard-stat-cards-the-pattern-new-modules-should-follow.md).

2. **The card's link (`/tasks/?overdue=1`) drives the board's own new
   "Overdue only" filter checkbox, not a separate one-off view.** ADR
   0019's rule ("the stat query should match the list page's own
   default filter, exactly") extends naturally here: rather than build
   a bespoke overdue-only page or duplicate the overdue condition in a
   second place, the board (ADR 0049/0050's client-side filter/sort
   system) gained one more filter -- `data-overdue="1"` alongside the
   existing `data-priority`/`data-due-month`/etc. -- and `board()`
   reads `?overdue=1` to pre-check that checkbox server-side. The
   dashboard count and what you land on after clicking are therefore
   guaranteed to agree, by construction, not by convention.

3. **The checkbox is a real, permanently-visible filter control, not a
   hidden query-param-only behavior.** Anyone can turn "Overdue only"
   on/off directly on the board without coming from the dashboard --
   consistent with every other filter already there, and more
   discoverable than a URL parameter with no corresponding UI.
