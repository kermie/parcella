# Club settings: board members

**Context:** GitHub issue #111 asked to "add the names of board members
to admin -> settings -> club settings. Use a member picker from members,
multiple options possible. Let me search for members in this member
picker."

**Decision:**

1. **A new `club_board_members` association table (`ClubBoardMember`
   model), not a `ClubSetting` value.** `ClubSetting` already has one
   list-shaped precedent -- `spam_domain_blocklist`/
   `spam_keyword_blocklist` (`app/spam_filter.py`) -- but that's a
   comma-separated list of free-text tokens with no referential
   integrity, not a list of entity references. Every real m:n
   relationship to a member elsewhere in this codebase
   (`InvoiceItemTemplateMember`, `InvoiceItemDefinitionMember`,
   `TaskAssignee`, `GroupMembership`) uses a dedicated table with its own
   `id` and a real FK, never a `secondary=` table and never a
   comma/JSON-encoded string of IDs. Board members follows the same
   shape: `id`, `member_id` (FK to `members.id`, `ON DELETE CASCADE`,
   since a board-member listing can't outlive the member), `created_at`.

2. **Deliberately not `ClubRole`/`MemberClubRole`
   (`app/routers/work_hours.py`).** That table already models
   "board"/"extended board" membership, but for work-hours exemption
   tracking -- year-scoped `valid_from`/`valid_until`, tied to
   `ExemptionReason.BOARD`/`EXTENDED_BOARD`. Reusing it here would
   conflate "who to list on the settings page today" with "who was
   exempt from work hours in a given year," which have different
   lifetimes and different admin surfaces. `ClubBoardMember` is a
   separate, simpler table with no date range: a member either is or
   isn't currently listed.

3. **Web UI: the finances `scope_picker` Jinja macro
   (`app/templates/finances/run_detail.html`, ADR 0042), not the task
   board's plain checkbox grid (ADR 0046).** Task assignees are picked
   from a small pool (board/admin users) and don't need search. Board
   members are picked from the full active-member list, which can run
   into the hundreds -- the same "pick a subset of a large entity list"
   shape that `scope_picker` (collapsible, searchable checkbox grid with
   select-all/select-none over currently-visible items) was built for.
   The macro isn't factored into a shared include (this codebase doesn't
   share Jinja macros across template files elsewhere either); it's
   duplicated into `app/templates/admin/settings.html` along with its
   supporting JS, matching how `run_detail.html` and
   `item_template_list.html` each carry their own copy.

4. **Update semantics: full resync, not incremental add/remove** -- same
   convention as `finances.py`'s `parcel_scopes`/`member_scopes` and
   `tasks.py`'s assignee resync (ADR 0046 point 4). `POST /admin/settings`
   deletes every existing `ClubBoardMember` row and re-inserts one per
   submitted `board_member_ids` value, intersected against the current
   active-member id set (a stale form -- a member deactivated between
   page load and submit -- would otherwise trip the FK constraint instead
   of just silently dropping that one id).

5. **API-first (ADR 0012): `GET /api/v1/club-settings/board-members`**,
   read-only (same `get_current_api_user` scope as the existing
   `GET /api/v1/club-settings` list) -- writing the list stays a web-form
   action via the admin settings page, not a REST endpoint, consistent
   with several other admin-only settings that also have no API write
   path. Registered *above* the existing `GET /{key}` route in
   `api_club_settings.py`, since a dynamic single-segment path pattern
   registered first would otherwise shadow `/board-members` (FastAPI
   matches routes in registration order).
