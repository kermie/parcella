# Cloud storage module (Nextcloud)

Connects Parcella to a club's existing Nextcloud instance so board/admin
users can browse, upload to, and download from a per-parcel document
folder without leaving Parcella -- lease agreements, membership
paperwork, ticket correspondence. Off by default (`cloud_storage`
module flag defaults to `False`), since it stores outbound credentials
and, once configured, lets board members move real member paperwork
in and out of the club's cloud storage.

## Data model

```
parcel_cloud_folders  -- one row per (parcel, tenancy period); at most
                          one active row per parcel at a time
```

**`ParcelCloudFolder`** holds `relative_path` (the folder's location
inside the club's Nextcloud account), `is_active`, and who set it
(`set_by_user_id`). Scoped to the **parcel**, not to a single
`MemberParcel` row -- a parcel can have several co-tenants (couples,
families) with separate `MemberParcel` rows for the same lease period,
and they share one folder. Older rows are kept (`is_active=False`) as
history rather than deleted, the same pattern as ended `MemberParcel`
assignments elsewhere in the project. A Postgres partial unique index
(`postgresql_where=is_active`) enforces "at most one active folder per
parcel" at the database level, not just in application code.

No new table is needed for credentials -- those live in the existing
`club_settings` key-value table (`nextcloud_base_url`,
`nextcloud_username`, `nextcloud_app_password`), same as the SMTP and
WordPress blog integrations.

## The connector: `app/cloud_storage.py`

Structured like `app/blog_publisher.py`: a small `CloudStorageProvider`
interface with one concrete implementation, `NextcloudProvider`, talking
WebDAV (`PROPFIND` to list, `PUT` to upload, `GET` to download, against
`{base_url}/remote.php/dav/files/{username}/...`). A future backend
(Google Drive, S3-compatible storage) would be a new class implementing
the same interface, not a change to this one.

Deliberately narrow for v1: **list, upload, download only** -- no
delete, no folder creation. The folder a club points Parcella at is
expected to already exist and typically is already shared with the
relevant members directly in Nextcloud. Board tooling deleting files
from someone's personal cloud storage is a bigger, separately-considered
decision than this module needed to make.

Every failure mode (network error, 401, 404, unexpected status,
unparseable XML) raises `CloudStorageError` with a message meant to be
shown directly to the board member who triggered the action -- not a
generic "something went wrong."

`load_nextcloud_configuration()` treats a partially-filled
configuration (e.g. URL and username saved, but no app password yet) as
"not configured" rather than attempting a connection and failing
confusingly.

## Lifecycle rules: `app/parcel_cloud_folders.py`

`sanitize_relative_path()` normalizes a board-entered path and rejects
anything containing a `..` segment -- the path is later joined onto a
WebDAV URL, so path traversal here isn't just a UI nuisance, it could
reach outside the intended folder tree on the Nextcloud side.

`deactivate_if_vacant(db, parcel_id)` is the one safety-relevant rule:
called after any action that can end a resident's tenancy (setting
`MemberParcel.assigned_until`), it deactivates the parcel's active
folder once the parcel has zero residents left with an open-ended
assignment. This runs automatically from `mitglied_zuordnung_
aktualisieren` and `mitglied_entfernen` in `app/routers/parcels.py` --
so a fresh set of tenants moving in after a full turnover never sees or
inherits the departing tenants' folder path. Re-configuring the folder
for the new tenancy is a **separate, deliberate action** a board member
takes afterwards; nothing points a new tenant at a folder automatically.

## Web UI and permissions

Viewing and mutating are both board/admin-only (`require_admin`, which
permits `ADMIN` and `BOARD` roles) -- unlike modules such as Inventory
where viewing is open to any member. A parcel's "Documents" card only
renders when the `cloud_storage` module flag is on *and* the logged-in
user is admin/board; the three mutating routes
(`POST /parcels/{id}/cloud-folder`,
`POST /parcels/{id}/cloud-folder/upload`,
`GET /parcels/{id}/cloud-folder/download`) are additionally gated with
`Depends(require_module("cloud_storage"))`, returning 404 if the module
is disabled.

Credentials are configured on **Admin -> Integrations**, alongside the
WordPress blog connection -- same page, same "test connection before
saving" pattern, same "leave the app password field blank to keep the
existing one" convention.

## Key decisions

**Parcella does not manage who can see a folder's contents.** Read/write
access to the actual files is granted directly in Nextcloud (shares),
independent of and invisible to Parcella. This is a deliberate scope
boundary, not an oversight: **ending a tenancy in Parcella deactivates
the folder *pointer*** (so the next tenant doesn't inherit it in the UI)
**but does not revoke the previous tenants' Nextcloud share.** A board
member has to do that by hand, directly in Nextcloud, today. This is a
known gap -- see "Still to do" below.

**Manual re-linking after a turnover, not automatic reuse.** Same
reasoning as `retired_at` in the inventory module: guessing that a new
tenant should inherit the old folder (or a folder at a derived path)
would be the system assuming intent it doesn't actually have. A board
member sets the new path once the new tenancy is confirmed.

**Quantity of implementation surface matches the actual request:**
list/upload/download covers "browse and get documents in and out";
nothing about in-app previewing, versioning, or comment threads was
asked for, and Nextcloud already does all of that natively for anyone
with a direct share.

## A WebDAV path-encoding bug found while building this

`_join_dav_path()` builds the URL path for a WebDAV request from
folder/file name segments. The first version quoted each **function
argument** as one opaque unit
(`quote(segment, safe="")`) -- correct for a single file or folder
*name*, but wrong when an argument is itself a full relative path like
`"kgv_dokumente/parzellen/G016"`: the internal `/` characters got
percent-encoded into `%2F` right along with the rest of the string, so
requests went to `.../parcels%2FG016` instead of `.../parcels/G016`.

This was non-obvious because Nextcloud's `PROPFIND` didn't error --
`%2F` inside a path segment just made the server treat the whole thing
as one (nonexistent-as-such) name, and depending on the exact
mock/server behavior it could still return *a* response, e.g. by
resolving to a parent collection. The bug surfaced as an unexpected
extra entry in the parsed file listing rather than a clean failure.
Fixed by splitting every argument on `/` before quoting each resulting
path component individually, so multi-segment relative paths and
single file/folder names both encode correctly. Covered by
`test_nextcloud_list_files_parses_propfind_response` in
`tests/test_cloud_storage.py`, which asserts the exact set of returned
entries rather than just "list_files doesn't raise."

## Still to do (v1.1 candidate)

**No reminder or automation to revoke the Nextcloud share when a
tenancy ends.** `deactivate_if_vacant()` only updates Parcella's own
pointer; the actual Nextcloud share (who can open the folder) is left
untouched. A club relying on this module needs a manual process today
("when a tenant leaves, remember to also revoke their Nextcloud share")
that Parcella doesn't currently prompt for. A future version could
surface a checklist item or banner on the tenancy-end flow
(`mitglied_entfernen` / the "remove" action in `app/routers/parcels.py`)
reminding the board member to go do this by hand in Nextcloud, without
Parcella attempting to manage Nextcloud shares directly (which would
need Nextcloud's separate Sharing API and credentials/permissions
scoped beyond WebDAV file access).
