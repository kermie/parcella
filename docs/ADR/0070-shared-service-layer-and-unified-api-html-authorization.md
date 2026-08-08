# Shared service layer for HTML/API duplication, and unified tickets authorization

**Context:** [Issue #195](https://github.com/parcella-garden/parcella/issues/195):
"treating API and HTML frontend differently becomes clumsy as the
development either has be done twice or it will differ more and more
over time." Every module has a parallel router pair
(`app/routers/<module>.py` HTML, `app/routers/api_<module>.py` API),
mandated by ADR 0012 -- but that ADR only requires both to exist, not
that they share logic. There was no service/CRUD layer in `app/`.

Auditing the `members` and `tickets` module pairs found this had already
drifted into real, shipping bugs, not just style debt:

- `active_member_filter()` (SQL, `app/database.py`) vs `Member.is_active`
  (Python property, `app/models.py`) are two independent
  reimplementations of "what counts as an active member" -- the
  property's own docstring cites a past real bug (issue #167) where they
  diverged.
- Tickets' status-transition rule was implemented twice: `_apply_status()`
  in the HTML router (i18n'd, HTTP 400) vs `status_update()` in the API
  router (hard-coded English, HTTP 422) -- same rule, already-diverged
  wording.
- `ChangeTracker` audit logging was wired into every ticket status/
  assignment change on the HTML side, but never on the API side --
  **API-driven ticket changes left no audit trail at all.** The most
  serious finding: a real, currently-shipping data-integrity gap.
- Assignment notification emails were localized via `t_for(...)` on the
  HTML side, hard-coded English on the API side.

Separately, and more serious: **HTML and API didn't just duplicate code,
they enforced different authorization rules for the same action.**
`app/permissions.py`'s `require_permission()` is fine-grained and
`Group`-based (per module, read/write/delete). `app/api_auth.py`'s
`require_write_access`/`require_api_role()` is coarse and role-only,
with no concept of `Group`/module scoping. This split was **deliberate**
(ADR 0038, ADR 0041 both explicitly excluded the API: "a separate
JWT-based role system... untouched"), but produced concrete divergence:
a TREASURER whose `Group` did **not** grant `tickets:write` was correctly
blocked in the HTML UI but could freely call `POST /api/v1/tickets` (the
role-only check passed regardless of `Group`). Conversely a READONLY
user granted write access via a `Group` could write via HTML but was
flatly blocked via the API (`Group` membership is invisible to
`require_api_role`). kermie confirmed this should be fixed as part of
this change rather than deferred.

## Decision

**1. Shared service layer, `app/services/<module>.py`.** Extends the
pattern `app/meter_utils.py` already established (pure functions
imported independently by both metering routers) to also cover
persistence, audit logging, and notification side effects -- not just
validation math.

- Routers own: authentication (session vs JWT), the fine-grained
  permission check (still at the router boundary -- see below), `Form(...)`
  vs Pydantic parsing, and response shaping (`RedirectResponse`/
  `TemplateResponse` + flash messages vs JSON `response_model`).
- Services own: business-rule validation, the `ChangeTracker` audit
  write, and notification email content -- built via `translate(key, lang, **kwargs)`
  (`app/i18n.py`) with an explicit `lang: str` parameter rather than a
  `Request`, so services stay testable without faking a request object.
- Services raise `ServiceError(key, http_status, **params)`
  (`app/services/errors.py`) instead of `HTTPException` directly --
  formalizes the `(translation_key, params)` shape `meter_utils.check_monotonicity()`
  already used informally. Each router renders the same i18n key its own
  way: HTML into a flash message at `e.http_status` (400), API always at
  422 (its existing convention for "well-formed request, business-rule
  violation" -- see `api_metering.py`) regardless of `e.http_status`.
  **Deliberately not unifying the HTTP status code** -- 400 vs 422 was
  the wrong axis to fix; the actual bug was the duplicated rule and the
  hard-coded English text, both now fixed by sharing the i18n key.
- Services call `db.flush()`, never `db.commit()`, so a router can batch
  several calls (bulk operations) into one commit.

**2. `app/services/tickets.py` -- the pilot module.** Chosen over
`members` because it has the real audit-trail gap, an
already-partially-shared function (`_apply_status`) to cleanly extract,
and exercises every category of drift found (business rules, i18n'd vs
hard-coded email, bulk-only-on-one-surface, and the permission split) --
the most representative single pilot. Extracted: `filtered_tickets_query`
(moved as-is), `create_ticket`, `change_status` (replaces `_apply_status`,
now includes the `ChangeTracker` write so it's structurally impossible
to change status without an audit row), `bulk_change_status`,
`assign_ticket`/`bulk_assign_tickets` (+ localized email), `set_member`,
`set_spam_status`/`bulk_set_spam_status`, `add_message`.

**3. `require_api_permission(module, level)`, `app/api_auth.py`.**
Fine-grained API counterpart to `require_permission()`, consulting the
same `get_user_permissions()`/`has_permission()` (`app/permissions.py`)
the HTML side uses, instead of the coarser role-only `require_api_role`.
Can't reuse `request.state.permissions`: `permissions_middleware`
(`app/main.py`) computes it via `get_current_user(request, db)`, which
only reads the `session` cookie -- always the anonymous baseline for a
JWT-authenticated API request. `require_api_permission` computes
`get_user_permissions()` directly per request instead. `app/routers/api_tickets.py`
uses it on all 9 endpoints (6 write, 3 read), replacing
`require_write_access`/bare `get_current_api_user`.

**Not touched in this pass:** `require_api_role`/`require_write_access`/
`require_admin_api` stay exactly as-is and keep gating every *other* API
router (13+ other call sites). No change to `permissions_middleware`/
`app/main.py` -- recomputing `get_user_permissions()` app-wide for every
API request is unnecessary blast radius for a one-module pilot. Nothing
about `app/public_api_auth.py` (the separate shared-secret CMS-plugin
mechanism) is affected.

## Behavior change on upgrade

This is a real production authorization change for the tickets API, not
just an internal refactor -- call it out explicitly rather than bundling
it silently into a patch release:

- **Tightens:** a TREASURER-role API user not in a `tickets:write`
  `Group` used to succeed on every ticket write endpoint; after this
  change, 403. An existing integration authenticating as such an account
  breaks. Mitigation: move that account into the right `Group`, or use
  an ADMIN/BOARD account (already full access, unconditionally, both
  before and after -- no change for those).
- **Loosens (pure bug fix, no risk):** a READONLY user granted
  `tickets:write` via a `Group` used to get 403 from the API despite
  succeeding via HTML; after this change, both agree.

## Not yet done -- tracked follow-up

**Update (2026-08-08):** a scoping survey ahead of the wider rollout
found this checklist wrong on scope in two ways, corrected below:
`calendar`, `announcements`, and `finances` have no `api_<module>.py`
at all -- there's no second router to diverge from, so this pattern
doesn't apply to them; and `tasks` already has the *correct*,
already-matched pair (`require_admin`/`require_admin_api`, both
admin/board-only by deliberate design -- `tasks` is intentionally not
in `app/permissions.py`'s `MODULES`), so `require_api_permission`
would be the wrong tool there; its one genuinely shared piece (kanban
drag-and-drop reordering) already lives in `app/task_board.py`,
predating this ADR's naming convention. Both are dropped from the list.

The remaining module pairs still have the same shape of duplication and
the same API/HTML authorization split. Migrating them (a shared
`app/services/<module>.py` + swapping that module's API router from
`require_write_access`/`require_api_role` to `require_api_permission`)
is follow-up work, module by module:

- [x] members (2026-08-08) -- mostly mechanical CRUD extraction; the one
      real bug closed was `active_only` filtering in Python *after*
      pagination instead of pushing `active_member_filter()` into SQL
      like the HTML side always did (could return fewer than the
      requested page size, or skip an active member depending on
      offset) -- the exact SQL-vs-Python drift class this ADR's intro
      cites (issue #167).
- [x] parcels (2026-08-08) -- the most serious finding after tickets
      itself: API-driven parcel edits (incl. status/termination) wrote
      no audit trail at all. Also found and fixed along the way: a
      former tenant could never be reassigned to the same parcel via
      the API (409'd on any historical row; HTML always reactivated),
      the invoice-address rule (issue #172) wasn't applied on HTML's
      brand-new-assignment path, and the API's single assignment DELETE
      endpoint used to hard-delete an *active* assignment unconditionally
      -- it now soft-ends an active one like HTML does, hard-deletes
      only an already-ended one.
- [ ] work_hours
- [x] insurance (2026-08-08) -- pure CRUD/query duplication, no audit
      trail or notifications involved on either side before or after;
      lowest-risk of the seven, mechanical extraction.
- [x] metering (2026-08-08) -- extended `app/meter_utils.py`'s already-
      shared validation/computation to also cover persistence, same
      medium-agnostic shape (ADR 0003's router-factory pattern). Real
      finding: the API resolved the monotonicity-check error via a
      German-only `format_monotonicity_error_de()` (now removed)
      instead of the shared i18n catalog HTML used -- always German
      regardless of the club's configured language.
- [x] purchase_requests (2026-08-08) -- highest-risk module (this
      repo's own documented highest-regression-risk area, per
      docs/testing.md): the four-eyes rule itself (self-approval block,
      2-distinct-approvals threshold) is now shared, but deliberately
      narrow -- the "already handled" short-circuits stay per-router
      since they weren't identified as a bug. Needed a new dependency,
      `require_api_full_access` (app/api_auth.py), since approval
      authority is Group-aware but narrower than ordinary write access
      -- `require_api_permission` would have been the wrong tool. Found
      the reverse-direction version of ADR 0071's TREASURER bug (API
      was *stricter* than HTML here, via role-only `require_vorstand_api`)
      and a broken confirmation email (no actual link for external
      requesters, told them to "log in" with no account to log into).
- [x] inventory (2026-08-08) -- category uniqueness, owner-type
      validation, and loan-quantity rules were each independently
      reimplemented; extracted with no audit trail/notification
      concerns either side. Three loan-checkout error messages that
      were hard-coded English on both sides got real translation keys
      (`inventory.errors.*`, all 7 languages) as a small bonus while
      centralizing them.
