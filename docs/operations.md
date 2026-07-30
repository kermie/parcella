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

A system admin can download a full database backup on demand from
`/admin/` ("Download backup" -- see
[ADR 0053](./ADR/0053-admin-backup-download-only.md)). It's a one-click
`pg_dump` (plain SQL, readable text) streamed straight to the browser --
nothing is ever written to server disk, so there's no backup file to
find or clean up on the server itself; the downloaded `.sql` file is the
only copy, and it's the admin's responsibility to store it somewhere
safe.

To restore a downloaded backup, e.g. into a fresh/empty instance:

```bash
docker compose exec -T db psql -U parcella -d parcella < parcella-backup-20260730-143000.sql
```

**Warning:** the backup was generated with `--clean --if-exists`, so the
script itself contains `DROP ... IF EXISTS` statements ahead of each
`CREATE` -- restoring it drops existing objects before recreating them.
Never run this against a live database whose current data you still
need -- restore into an empty database (or a throwaway one) instead,
then swap it in deliberately.

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
