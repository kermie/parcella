# Scheduled cloud backups: reversing the cloud-storage connector's no-delete stance

**Context:** Issue #141 extends the manual local backup/restore feature
(issue #117, ADR 0053/0054) to the club's already-connected Nextcloud
integration: automatic uploads on a schedule, a configurable
destination folder, retention (keep the newest N, prune older), and a
picker to restore directly from a cloud-stored backup -- all without
relying on Linux cron ("use the next action in this app").

**Decision 1: `delete_file`/`create_folder` added to
`CloudStorageProvider`, narrowly.** [ADR 0033](./0033-cloud-storage-module-nextcloud.md)
deliberately shipped list/upload/download only, reasoning that the
folder a club points Parcella at should already exist and that
deleting files from board tooling was a bigger, separately-considered
decision. This feature needs both: it must create its own destination
folder on first run (an admin shouldn't have to go create it in
Nextcloud by hand first) and prune backups beyond the configured
retention count. Scope is narrow on purpose -- these two methods exist
so *this feature* can manage *its own* backup files in *its own*
configured folder; nothing else in the app gained delete/create
access to arbitrary cloud paths.

`NextcloudProvider.create_folder` (`app/cloud_storage.py`) MKCOLs each
path segment in turn -- WebDAV's MKCOL 409s if an intermediate parent
doesn't exist yet, it does not create nested collections in one call.
A 405 (segment already exists) is treated as success, making the whole
call idempotent and safe to run before every backup. `delete_file`
treats a 404 (already gone) as success for the same reason -- the
retention sweep must tolerate a backup a previous, partially-failed
sweep already removed.

**Decision 2: a 15-minute tick-based scheduler, not a long sleep.** The
existing background loops (`app/main.py`'s ticket-mailbox poll and
update check) each sleep for one fixed duration matching their single
purpose. This feature's frequency (hourly/daily/weekly/monthly) is
admin-configurable at runtime, so a long `asyncio.sleep` sized to the
*current* setting couldn't react to a changed setting without an app
restart. `_cloud_backup_polling_loop` instead ticks every 15 minutes
and calls `is_backup_due()` (a pure function, no I/O) to decide whether
to actually run -- cheap enough to check every tick, fine-grained
enough that even "hourly" fires within about 15 minutes of on-time.
Frequency-to-seconds is a fixed approximation (`monthly = 30 days`),
consistent with the existing loops' non-calendar-aware style -- no
attempt to align to specific wall-clock dates.

**Decision 3: retention is count-based, not age-based.** "Keep the
newest N, delete the rest" -- sorted on the existing
`parcella-backup-{timestamp}.zip` naming convention, which sorts
lexicographically in the same order as chronologically, so no date
parsing is needed. Simpler than a max-age policy and matches what the
issue actually asked for ("how many backups will be hold there").

**Decision 4: restore-from-cloud is not a new destructive code path.**
`backup_restore_from_cloud` (`app/routers/admin.py`) downloads the
chosen backup from Nextcloud and calls the exact same
`restore_from_zip_bytes()` (`app/backup.py`) that the manual-upload
restore uses -- same `RESTORE_CONFIRM_PHRASE` type-to-confirm
safeguard, same `--single-transaction` atomicity, same uploads
mirror-replace. The only difference between the two restore entry
points is where the zip bytes come from.

**Mechanics: the `app/backup.py` extraction.** Sharing `build_backup_zip`/
`restore_from_zip_bytes` between the manual HTTP flow and this
unattended scheduler required pulling them out of
`app/routers/admin.py` (which is FastAPI/HTTP-specific) into a new,
framework-free `app/backup.py`. `restore_from_zip_bytes` takes no
`db`/`Request` at all -- closing the caller's own DB session (the
self-deadlock fix from ADR 0054: `require_system_admin`'s open
transaction conflicts with `--clean`'s `DROP TABLE`) stays the
*caller's* responsibility, done immediately before calling the shared
function, since only the caller knows which session it's holding.

**`run_cloud_backup_now` never raises.** Both the scheduler loop and
the manual "back up to cloud now" button call this exact function, which
always records `last_run_at`/`last_run_status`/`last_run_error` in
`ClubSettings` -- even a "Nextcloud isn't configured" case gets an
explanatory error recorded, not a silent no-op, so a manual click
always tells the admin why nothing happened. One source of truth, read
back by whichever caller needs to react to it, rather than two
error-reporting paths that could disagree.

**Settings storage:** plain `ClubSetting` rows
(`cloud_backup_enabled`/`_folder`/`_frequency`/`_retention_count`/
`_last_run_at`/`_last_run_status`/`_last_run_error`), matching the
existing `update_check_*` convention exactly (boolean as literal
`"true"`/`"false"`, timestamp as `.isoformat()`) -- no new table, no
migration.
