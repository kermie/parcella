# Operations

Practical commands and troubleshooting for day-to-day operation.

## Basic Docker commands

```bash
# Build the container (needed after changes to requirements.txt or Dockerfile)
docker compose build web

# Start the container
docker compose up -d

# Restart the container (sufficient for pure Python code/template changes,
# since uvicorn runs in --reload mode)
docker compose restart web

# View logs
docker compose logs web --tail=30

# Check status
docker compose ps
```

## Database migrations

```bash
# Apply migrations (also runs automatically on container startup)
docker compose run --rm --entrypoint alembic web upgrade head

# Generate a new migration after a model change
docker compose run --rm web alembic revision --autogenerate -m "Short description"

# Check the current state
docker compose run --rm --entrypoint alembic web current

# Check all "heads" (in case of a "Multiple head revisions" error)
docker compose run --rm --entrypoint alembic web heads
```

**Important:** revision names (`revision: str = "..."`) must stay under
32 characters -- the `alembic_version` table has a `VARCHAR(32)` column.

**On a "Multiple head revisions" error:** usually caused by two migrations
created in parallel with the same `down_revision`. Fix: delete one of the
two migration files, and if necessary correct the `alembic_version` entry
in the DB directly:
```bash
docker compose exec db psql -U parcella -c "UPDATE alembic_version SET version_num = '<correct_revision>' WHERE version_num = '<wrong_revision>';"
```

## SMTP setup

SMTP credentials can be entered under `/admin/settings` (the database
takes precedence) or via the `.env` file (fallback if DB values are
missing):

```
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=user@example.com
SMTP_PASSWORD=...
SMTP_FROM=verein@example.com
SMTP_TLS=true
```

The SMTP password is stored encrypted in the database (see
[Architecture Decisions](./ADR/0006-passwords-hashing-vs-encryption.md)). An SMTP
server can safely be configured even while the app is still running
under `localhost` -- sending mail is an outbound connection from the
container to the mail server, independent of how the app itself is
reached.

## Backups & restore

A system admin can download a full backup on demand from `/admin/`
("Download backup" -- see
[ADR 0053](./ADR/0053-admin-backup-download-only.md)). It's a zip
containing a one-click `pg_dump` (plain SQL, readable text) plus
everything under `app/static/uploads/` (the branding logo, announcement
images) -- nothing is ever written to server disk, so there's no backup
file to find or clean up on the server itself; the downloaded `.zip` is
the only copy, and it's the admin's responsibility to store it
somewhere safe.

**Automatic backups to a connected cloud solution:** `Admin -> System ->
Cloud backups` (`/admin/backup/cloud` -- see
[ADR 0055](./ADR/0055-scheduled-cloud-backups.md)) can upload that same
zip to a folder in your connected Nextcloud on a schedule
(hourly/daily/weekly/monthly), keeping only the newest N backups and
pruning older ones automatically. Requires the `cloud_storage` module
enabled and a working Nextcloud connection under **Admin ->
Integrations** first. No Linux cron involved -- the schedule is an
in-process check every 15 minutes, same style as the update-check and
ticket-mailbox polling loops.

**Restoring, the normal way:** a system admin can also upload that same
zip back through `/admin/backup/restore` ("Restore from backup" -- see
[ADR 0054](./ADR/0054-admin-restore-from-backup.md)) and restore both
the database and `app/static/uploads/` in one step -- or, from the
Cloud backups page, restore directly from any backup already sitting
in the cloud, without downloading and re-uploading it by hand. Both are
destructive, irreversible actions -- they replace the entire current
database and every uploaded file with the backup's contents -- so both
require typing the literal confirmation phrase `RESTORE` before running
anything. If you want to be able to undo a restore, download a fresh
backup of the current state *first*.

**Restoring manually, e.g. when the app itself is unreachable** (container
won't start, admin panel broken) and the UI isn't an option:

```bash
unzip parcella-backup-20260730-143000.zip -d restore/
docker compose exec -T db psql -U parcella -d parcella < restore/parcella-backup-20260730-143000.sql
cp -r restore/uploads/. app/static/uploads/
```

**Warning:** the backup was generated with `--clean --if-exists`, so the
SQL script itself contains `DROP ... IF EXISTS` statements ahead of each
`CREATE` -- restoring it drops existing objects before recreating them.
Never run this against a live database whose current data you still
need -- restore into an empty database (or a throwaway one) instead,
then swap it in deliberately. The in-app restore path guards against
this with the confirmation phrase; the manual command above has no such
guard, so treat it with the same care.

Neither restore path runs `alembic upgrade head` for you -- see the next
point, which applies identically whichever path you use.

**What the backup does *not* cover -- read this before relying on one:**

- **Encrypted fields don't travel.** SMTP/WordPress/Nextcloud
  credentials are stored encrypted with a key derived from `SECRET_KEY`
  (`app/crypto_utils.py`), which lives only in `.env`, deliberately
  outside the database and outside this backup. Restore a dump into an
  instance whose `SECRET_KEY` differs from the one that created it, and
  those fields decrypt to garbage -- you'll need to re-enter that
  handful of credentials by hand afterward. Keep a copy of `.env`
  (or at least `SECRET_KEY`) alongside your backups if you ever expect
  to restore onto different infrastructure.
- **Restoring an older backup into a newer Parcella version:** should
  work, but isn't automatic. Restore the dump, then run
  `docker compose run --rm --entrypoint alembic web upgrade head` --
  migrations are additive and forward-only, so as long as no migration
  file has been rewritten after release (this project never does that),
  Alembic will bring the restored schema up to whatever version you're
  currently running. The one sharp edge: a migration that does a raw
  `ALTER TYPE ... ADD VALUE` for an enum has to get the value's *name*
  casing exactly right (see the enum sharp edge in
  [CLAUDE.md](../CLAUDE.md)) -- this has been gotten wrong once in this
  codebase's history, and Postgres has no way to undo a bad
  `ADD VALUE` afterward.
- **Test the restore path itself, occasionally, on a throwaway
  database** -- rather than assuming a backup is good just because the
  download succeeded. A backup you've never restored is a hypothesis,
  not a guarantee.

## First login

On the very first startup (empty `users` table), an admin account is
created automatically:

- Email: `admin@parcella.local`
- Password: `admin1234`

Please change it immediately after your first login.

## Common failure patterns

| Symptom | Likely cause |
|---|---|
| `invalid input value for enum` | Enum value in Python != enum value in DB (case mismatch) |
| `MultipleResultsFound` | `scalar_one_or_none()` used on a query that can return multiple hits |
| `MissingGreenlet` on start/restart | `scalar_one_or_none()` on a table with multiple rows (e.g. a user-count check) |
| `MissingGreenlet` on a single page | Lazy-load on a freshly created object without eagerly loaded relationships |
| CSV import: every row shows "error" | Delimiter mismatch (Excel may save with comma instead of semicolon) |
| Docker: root-owned files in the project folder | Container ran as root; set `UID`/`GID` in `.env` (see `docker-compose.yml`) |
