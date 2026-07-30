# Admin backup: on-demand download only, no server-side storage

**Context:** Issue #117, "add a backup possibility to administration
panel" -- previously there was no backup feature of any kind (no
`pg_dump` code, no documented procedure). Three real design choices had
to be made, none of them obvious in hindsight, so they're recorded here
rather than left for a future maintainer to reverse-engineer.

**Decision 1: direct download, nothing ever written to server disk.** An
admin clicks a button; the server runs `pg_dump` and returns the result
straight to the browser as a file attachment (`app/routers/admin.py`,
`backup_download`). No backup file is ever saved to a server directory or
volume. The alternative -- store backups server-side with a retention
policy and a "past backups" list in the admin panel -- was considered and
rejected: this app's database is the single most sensitive dataset in the
system (every member's personal data plus club finances in one file), and
a stored-on-server design multiplies the ways that data can leak (wrong
file permissions, a misconfigured volume mount, a forgotten cleanup job
leaving years of backups sitting around). Download-only means there is
nothing an attacker who compromises the `web` container's filesystem can
find after the fact -- the data only ever exists in the admin's own
browser download and wherever they choose to store it next.

**Decision 2: gated by `require_system_admin` only, no module flag.**
Every other pure admin-panel operational action in this app (the update
check-now button, sample-data add/remove) is gated the same way, per
`app/permissions.py`'s explicit statement that the admin panel is
carved out of the module/permission system entirely and "stay[s]
admin/board-only regardless of group configuration." A backup covering
the whole database is exactly this category of feature, not a
per-association-configurable module area, so it gets no new
`modul_backup`-style flag in `app/module_flags.py`.

**Decision 3: plain SQL (`pg_dump`'s default format), not custom
format.** Custom format (`-F c`) was the initial choice -- built-in
compressed, restorable selectively via `pg_restore` -- but it produces a
binary file with no readable content, which surprised the admin who
actually downloaded one (reasonably: `pg_dump` ships as plain SQL by
default, and this app's admins are club board members, not necessarily
DB experts). Plain SQL means the file can be opened in a text editor and
restored with a plain `psql`, at the cost of no built-in compression and
no selective restore -- both worth giving up for a feature whose whole
point is "an admin can understand and use this without extra tooling
knowledge." `--clean --if-exists` is passed to `pg_dump` so the emitted
script includes `DROP ... IF EXISTS` ahead of each `CREATE`, keeping it
restorable directly into a database that already has the same objects
(the equivalent of `pg_restore --clean --if-exists`, but embedded in the
script itself since plain SQL has no separate restore-time flags).

**Mechanics, for completeness:** `pg_dump` runs from inside the `web`
container against the `db` service over the network (same pattern as
Alembic migrations already running from `web`), using connection details
parsed from `settings.database_url`. The `postgresql-client` package is
installed via the Dockerfile's own Debian (trixie) repo -- its `pg_dump`
(v17) dumping the `db`/`db_test` services' PostgreSQL 16 is a standard,
fully-supported combination, since `pg_dump` is backward-compatible with
older servers; no separate PGDG apt source was needed. The database
password is passed to the subprocess via its environment (`PGPASSWORD`),
never as a CLI argument or connection-string URI, so it never appears in
a process listing. The full dump is buffered in memory
(`process.communicate()`) and only returned once `pg_dump` has exited
successfully -- deliberately not `StreamingResponse`, so a failed dump
never partially streams a corrupt file that looks like a successful
download.

**Out of scope:** there is no restore-from-UI feature. Restoring a
downloaded backup is a deliberately manual, `psql`-on-the-command-line
operation (see [Operations](../operations.md#backups--restore)) --
building a restore-from-upload feature would mean accepting an arbitrary
SQL/binary file from an admin's browser and feeding it to `psql`/
`pg_restore` against the live database, a much larger blast-radius
feature than this issue asked for.
