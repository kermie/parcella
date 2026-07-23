# Admin panel is ADMIN-only; BOARD is full module access, not panel access

**Context:** `require_admin` (`app/auth.py`) originally let both `ADMIN`
and `BOARD` roles through, for everything from `/admin/*` (user
management, groups, club settings, integrations) down to per-module
actions like approving a purchase request or managing a Nextcloud
folder. In practice this conflated two different roles a real
allotment association actually has: people who administer the
*application itself* (not necessarily council members -- could be IT
volunteers with no club office) and the *council/board*, who need full
operational access to every club module but have no business in -- and
no need for -- the app's own configuration.

**Decision:** Split into two dependencies:
- `require_admin` (unchanged name, narrowed meaning): `ADMIN` or
  `BOARD` -- full read/write/delete on every club module. Still used
  by `announcements.py`, `tasks.py`, `purchase_requests.py`'s
  approve/reject, and `parcels.py`'s cloud-storage actions -- none of
  that is "administering the installation," it's council business, so
  BOARD keeps it unchanged.
- `require_system_admin` (new): `ADMIN` only. Now guards the entire
  admin panel -- `app/routers/admin.py` and `app/routers/admin_groups.py`
  -- user invite/edit/deactivate, club settings, integrations, sample
  data, and group management. The "Administration" nav section
  (`base.html`) is hidden from BOARD accordingly.

`ADMIN` still gets full module access too (via `get_user_permissions`
in `app/permissions.py`, unchanged) -- an Administrator sees and can do
everything; a Board member sees and can do everything except configure
the application.

**Last-admin guard narrowed to last-ADMIN, not last-ADMIN-or-BOARD.**
`app/routers/admin.py`'s `_is_last_admin` (previously
`_is_last_admin_capable`) now only counts active `ADMIN` users. Losing
the last BOARD account isn't a lockout -- any remaining ADMIN can
promote someone via the panel BOARD can no longer reach anyway. Losing
the last ADMIN *is* a lockout (nobody could reach `/admin/` to fix
it), so that's the invariant actually worth protecting, on both the
deactivate and edit-role code paths.

**TREASURER/READONLY now get a baseline: read-only on
`members_parcels`, always.** Previously both roles started from
*zero* access to all 9 group-governed modules (`app/permissions.py`'s
`_EMPTY_PERMISSION`) -- a group had to be created just to let someone
look up a member or parcel, and the groups-page copy claiming groups
give "narrower access than the default" was backwards from what the
code did (the real default was nothing, not something broad to
narrow). Now `get_user_permissions` seeds `members_parcels.read = True`
unconditionally for any non-ADMIN/BOARD user before layering group
permissions on top, matching the actual intended model: a
focus-group member (e.g. "responsible for Water") can always look up
who's who and which parcel is which, and a group grants them
read/write/delete on their specific area on top of that -- it widens
from a small baseline, it doesn't narrow from a large one. The
groups-page intro text was corrected to describe this accurately in
all seven languages.

**Not addressed here:** hard-deleting a `User` row (vs. the existing
deactivate toggle) -- see ADR 0040.
