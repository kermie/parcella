# Groups supersede roles for new user access

**Update (2026-08-07):** one place TREASURER kept role-based privilege
past this ADR's "additive, not a replacement" intent -- the REST API's
`require_write_access` -- has been closed. See
[ADR 0071](./0071-treasurer-role-privilege-retired-groups-only.md).

**Context:** `UserRole` (ADMIN/BOARD/TREASURER/READONLY) is a fixed
enum baked into the app -- but no two allotment associations are
structured the same way (this one has 5 distinct council roles;
another could look completely different). ADR 0039 already split
"reaches the admin panel" from "full rights on every module," and gave
Treasurer/Read-only a baseline -- but the underlying problem is that a
*role* is still the only way to grant meaningful access, and it's a
fixed, hardcoded shape. The user's proposal: stop picking a role when
adding someone, and assign them to group(s) designed upfront instead
-- an "Administrators" group (full rights + admin panel), a "Board"
group (full rights, no panel), a "Wasserwart" group (read on core,
full rights on Water only), etc.

**Decision: additive, not a replacement.** `role in (ADMIN, BOARD)`
keeps working exactly as it does today for whoever already has it --
existing installs, existing tests, and above all the bootstrap
`Administrator` account seeded on first install (`app/main.py`) are
completely unaffected. What's new is that a `Group` can now *also*
grant the same effective access a role used to be the only way to
get:

- `Group.grants_full_access` -- full read/write/delete on every
  module (today's BOARD behavior).
- `Group.grants_system_admin` -- also reaches the admin panel
  (today's ADMIN behavior). Implies full module access regardless of
  `grants_full_access`'s own value, so an "Administrators" group only
  needs the one checkbox.

`app/permissions.py`'s `is_full_access_user`/`is_system_admin_user`
check role first (cheap, unchanged behavior), then fall back to group
membership. `app/main.py`'s `permissions_middleware` computes both
once per request and caches them on `request.state`, exactly like it
already did for per-module permissions -- `require_admin`/
`require_system_admin` (`app/auth.py`) and every other place that used
to inspect `user.role` directly (purchase request approval, calendar
council-absence deletion, the cloud-storage section, several nav
items) now read the cached flag instead, so "group-granted full
access" and "role-granted full access" are indistinguishable
everywhere in the app.

**The bootstrap account is the permanent escape hatch, not a new
mechanism.** The seeded `Administrator` account already solves the
chicken-and-egg problem of creating the very first group -- no new
bootstrap concept was needed. Migration 0039 seeds two starting
groups, "Administrators" (`grants_system_admin`) and "Board"
(`grants_full_access`), pre-populated with whoever currently holds
that role -- so upgrading an existing install produces exactly the
two example groups this feature was designed around, already
populated and ready to rename or extend.

**Invites assign groups, not a role.** `Invitation.role` is kept
(avoids touching the `userrole` DB enum or the accept-invitation code
path) but is always `READONLY` now -- an inert default. A new
`InvitationGroupTarget` join table (mirrors `GroupMembership`'s shape)
records which group(s) an invite will place the user into once
accepted; `routers/auth.py`'s accept-invitation route creates the
matching `GroupMembership` rows right after creating the `User`.

**No new role grants through the UI.** The invite form and the
edit-user page no longer offer ADMIN/BOARD as a role to assign --
that's now exclusively what groups are for. An existing legacy
ADMIN/BOARD account can still be demoted back to a plain
group-managed account (`role=READONLY`) from its edit page, guarded by
the same lockout check described below, but nothing can be newly
promoted to ADMIN/BOARD outside of direct database access.

**Lockout guard extended to cover groups, and shared.** Deactivating,
demoting, or deleting the last ADMIN was already guarded
(`is_last_admin`, ADR 0039) -- now that a group can grant the same
admin-panel access, removing the last member of the last
`grants_system_admin` group (via `admin_groups.py`'s member-remove,
or turning the flag off in `group_update`, or `group_delete`) is
exactly as dangerous and needed the same protection, which didn't
exist before this change. `is_last_admin` moved from
`app/routers/admin.py` into `app/permissions.py` so both routers share
one implementation instead of two guards drifting apart.

**Explicitly out of scope, matching the ADR 0038 precedent:** the REST
API (`app/api_auth.py`, `require_admin_api`, `require_write_access`,
`api_purchase_requests.py`'s `require_vorstand_api`) stays role-only,
unchanged. It's a separate JWT-based system this project has already
deliberately excluded from the group-permission work once; group-based
access there would need its own follow-up if ever wanted.
