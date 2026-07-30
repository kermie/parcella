# Admin restore from backup: reversing ADR 0053's "no restore-from-UI"

**Context:** [ADR 0053](./0053-admin-backup-download-only.md) explicitly
ruled out a restore-from-UI feature when the backup-download feature was
built, on the grounds that accepting an arbitrary file from a browser and
feeding it to `psql`/`pg_restore` against the live database was a much
larger blast-radius feature than a read-only download button. The user
has now explicitly asked for exactly that, having weighed the tradeoff
themselves. This ADR documents the reversal and the safeguards judged
sufficient to accept it -- it does not edit 0053's text, which stands as
an accurate record of what was decided and why, at the time.

**Decision: build it, gated by three safeguards specific to how
destructive it is.**

1. **Type-to-confirm, not a checkbox.** The admin must type the literal
   phrase `RESTORE` into a text field, checked server-side
   (`backup_restore` in `app/routers/admin.py`) before anything is even
   opened, let alone executed. A fixed, untranslated phrase avoids i18n
   edge cases (case/accent differences across languages) while still
   requiring a deliberate act, not just a click.

2. **`--single-transaction -v ON_ERROR_STOP=1` on the `psql` restore.**
   A failure at any point rolls back the entire script -- including any
   `DROP`/`CREATE` statements that already ran -- so a bad restore
   attempt leaves the database completely untouched rather than
   half-replaced. Confirmed safe against the enum-casing sharp edge in
   the top-level `CLAUDE.md`: that sharp edge is about a *hand-authored*
   Alembic migration's `ALTER TYPE ... ADD VALUE` against an
   *already-existing* enum type (which cannot run inside any transaction
   at all, by Postgres's own rules). A fresh `pg_dump` plain-SQL dump
   never emits that -- it emits one `CREATE TYPE x AS ENUM (...)`
   listing every value at once. The two are orthogonal; this feature
   never triggers that sharp edge. Also confirmed: a single-database
   `pg_dump`'s plain output has no `\connect` meta-commands (a
   `pg_dumpall`-only feature), so nothing mid-script can reconnect and
   silently defeat `--single-transaction`; and this codebase uses no
   Postgres extensions, so no `CREATE EXTENSION` (which needs superuser)
   hides in a dump either.

3. **Zip-slip protection, hand-rolled rather than `ZipFile.extractall()`.**
   Every `uploads/...` member's resolved path is checked against
   `UPLOAD_DIR` before anything is written (`_assert_within_upload_dir`,
   `app/routers/admin.py`) -- reject on any escape attempt, don't
   silently reinterpret it. This matters because Python's `extractall()`
   does *not* leave a `..`-containing member name alone or refuse it: it
   silently rewrites the path to strip `..`/`.`/drive-letter components
   before extracting, which is the wrong failure mode here -- we want a
   malformed backup rejected outright, not quietly reinterpreted.
   Writing bytes via plain `open(..., "wb")` (never `ZipInfo.extract()`)
   also means a crafted symlink-type zip entry can never become an
   actual symlink on disk, a variant path-traversal protection based
   purely on resolved-path checks doesn't automatically cover.

**Full mirror of `app/static/uploads/`, not an additive overlay.** Once
the database is rolled back to the backup's state, any uploaded file
*not* in the backup is an orphan by definition -- nothing in the
restored database references it. `_mirror_replace_uploads` builds the
new tree in a sibling temp directory and swaps it in via a single
`rename()`, so the window where `UPLOAD_DIR` is missing/partial is one
syscall rather than however long the file copy takes.

**Order: database first, then uploads.** If `psql` fails,
`--single-transaction` guarantees nothing changed at all -- filesystem
never touched. If the database restore succeeds but the uploads
mirror-replace then fails (e.g. disk full), that's a real but
recoverable partial state: both steps are individually idempotent, so
the admin is told explicitly that the database is already restored and
re-running the whole restore is safe (`admin.restore.error_uploads_failed`).

**Three risks specific to this feature, not present in the read-only
download feature:**

- **Self-deadlock.** `require_system_admin` runs a real `SELECT` through
  the request's own `db` session, which holds an open transaction (at
  least `ACCESS SHARE` on `users`) until closed. `psql --clean`'s
  `DROP TABLE users` needs `ACCESS EXCLUSIVE`, which conflicts with
  *our own* request's lock -- left alone, the restore would hang until
  the subprocess timeout and misreport a false failure. Fixed by
  `await db.close()` immediately after the admin check, before `psql`
  runs; the handler doesn't need the ORM session again.
- **Stale prepared-statement cache.** After `--clean` drops and
  recreates every table (new object IDs), a pooled asyncpg connection
  still holding a prepared-statement plan from before the restore can
  throw `cached plan must not change result type` on its next query.
  Fixed by `await engine.dispose()` (from `app.database`) immediately
  after a successful restore, dropping every pooled connection so
  nothing stale gets reused.
- **Client/server version skew.** Found empirically, by actually
  running a restore round-trip in tests (not by inspection): the
  Dockerfile originally installed the plain `postgresql-client`
  meta-package (v17, from Debian trixie's own repo), on the reasoning
  that `pg_dump` is backward-compatible with older servers -- true for
  *dumping* a v16 server, but not sufficient for restoring back into
  it. A v17 `pg_dump` embeds a `SET transaction_timeout = 0;` preamble
  line (a v17-only GUC) that a v16 server's `psql` rejects outright:
  `unrecognized configuration parameter "transaction_timeout"`. Fixed
  by pinning to `postgresql-client-16` via the PGDG apt repository
  (confirmed available for Debian trixie), matching the `db`/`db_test`
  services' PostgreSQL 16 exactly -- see the correction added to
  [ADR 0053](./0053-admin-backup-download-only.md)'s mechanics section.
  This is exactly the kind of thing that only a real round-trip test
  against real Postgres catches (per `docs/testing.md`'s philosophy) --
  no amount of reading the version-compatibility documentation would
  have surfaced it ahead of time.

**Out of scope, still:** no automatic pre-restore safety backup is taken
server-side -- that would mean writing something to server disk, which
[ADR 0053](./0053-admin-backup-download-only.md) already rejected for
good reason. The restore page's warning copy instead tells the admin
directly: download a fresh backup of the current state first if you
want a way back.
