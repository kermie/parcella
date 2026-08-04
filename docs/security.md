# Security

What protects this app, what a review found, and what is knowingly still
open. Written after a full-codebase review in August 2026; keep it
current when you touch anything below.

## What the app relies on

| Area | Mechanism |
|---|---|
| Passwords | bcrypt (`app/auth.py`); never encrypted-and-reversible, see [ADR 0006](./ADR/0006-passwords-hashing-vs-encryption.md) |
| Web sessions | signed cookie (itsdangerous), `HttpOnly` + `SameSite=Lax` + `Secure` outside development, 8h max age |
| REST API | JWT bearer tokens, HS256, separate role system (`app/api_auth.py`) |
| Public signup API | one shared installation token in a custom header + honeypot + per-IP rate limit ([module doc](./module-public-api.md)) |
| ICS feeds | shared installation token, constant-time comparison (`app/ics_utils.py`) |
| Login | failure-only throttle per IP and per IP+account ([ADR 0065](./ADR/0065-credential-and-deployment-hardening.md)) |
| Forged requests | `SameSite=Lax` **plus** a double-submit CSRF token ([ADR 0064](./ADR/0064-csrf-and-security-response-headers.md)) |
| Response headers | CSP, nosniff, `X-Frame-Options`, referrer policy, HSTS outside development |
| Untrusted HTML | bleach, twice: on ingest and again at render (`app/html_sanitizer.py`) |
| Stored secrets | Fernet, key derived from `SECRET_KEY` (`app/crypto_utils.py`) |
| Backup restore | hand-rolled zip validation with a zip-slip guard, deliberately not `extractall` (`app/backup.py`) |
| SQL | SQLAlchemy only -- there is no raw SQL string building anywhere in `app/` |

## What the review fixed

1. **Stored XSS via a member's address.** `members/detail.html` rendered
   `address_lines(...)|join('<br>')|safe`. Jinja's `join` returns a
   plain `str` for plain-str items, so `|safe` marked raw member input
   as trusted. Replaced by `address_html()` (`app/l10n.py`), which
   escapes each line and joins with a `Markup` separator.
2. **`/api/v1/stats` was unauthenticated** -- member/parcel counts and
   areas for anyone who could reach the port. Now behind the same JWT
   dependency as every other API router, declared on the router.
3. **The published default `SECRET_KEY` could run in production.** The
   app now refuses to start unless `ENVIRONMENT=development`.
4. **No brute-force protection on either login endpoint.** Added, shared
   between the web and API entry points.
5. **The bootstrap admin's documented default password was permanent.**
   Now `must_change_password`: the web UI redirects to the change form
   and the API issues no token until it's replaced.
6. **No CSRF tokens and no security headers.** Both added; see ADR 0064.
7. **36 dependency advisories across 10 packages** (pip-audit), reduced
   to the residual list below. The one that mattered most: `bleach`
   6.1.0 carried two XSS-bypass advisories, and bleach is what sanitizes
   ticket email HTML from arbitrary external senders.
8. **CSV formula injection in the finance bookings export** (flagged by
   an external pentest of the deployed instance, not found in-house).
   `reference`/`description`/`counterparty` were written to the export
   verbatim; a value starting with `=`, `+`, `-`, or `@` is read as a
   formula by Excel/LibreOffice Calc when the file is opened, letting
   whoever entered the booking run code on the workstation of the
   finance user who opens it. The same pattern existed in the member,
   parcel, and work-hours-evaluation CSV exports (free-text notes,
   addresses, member names). Fixed with `app/csv_utils.csv_safe()`,
   applied to every free-text cell in those four exports; the insurance
   CSV export was audited too but only ever writes numbers and
   admin-picked package names, so it was left alone.

Regression tests for all of these live in `tests/test_security.py`.

## Known and still open

Nothing here is believed to be exploitable today, but each is a real
limitation someone should weigh before relying on the app in a bigger
setting.

- **Starlette is pinned below 1.x**, which leaves five advisories
  (`PYSEC-2026-161/248/249/2280/2281`) unfixed: URL reconstruction from
  the `Host` header and from paths, a Windows-only `StaticFiles` SSRF,
  and `HTTPEndpoint` method lookup. Moving to 1.x requires migrating
  every `TemplateResponse(name, context)` call in every router to the
  new `(request, name)` signature. Partially mitigated: the middlewares
  that make security decisions read `request.scope["path"]`, the same
  value routing uses, rather than the reconstructed `request.url`.
- **`ecdsa` 0.19.2** (a transitive dependency of `python-jose`) has an
  unfixed Minerva timing advisory. Not reachable here -- tokens are
  HS256, no ECDSA keys involved. Migrating from `python-jose` to `PyJWT`
  would remove the dependency entirely.
- **The CSP still allows `'unsafe-inline'`** for scripts and styles,
  because the templates are full of inline blocks. See ADR 0064.
- **Rate limits are in-memory and per-process.** They reset on restart
  and aren't shared across workers. Behind a proxy they only distinguish
  callers if uvicorn runs with `--proxy-headers` (see
  [operations](./operations.md)).
- **Sessions can't be revoked.** The session cookie is a stateless
  signed token: changing a password does not invalidate sessions that
  are already open, and logging out only clears the cookie on that one
  browser. Deactivating an account *is* effective immediately (checked
  on every request). A fix would mean a token version per user, checked
  on each request.
- **Purchase-request confirmation links don't expire** and aren't rate
  limited (`/purchase-requests/confirm/{token}`).
- **Upload size limits are checked after the body is read**
  (`app/avatars.py`, `app/routers/announcements.py`).
- **`crypto_utils.decrypt()` returns the ciphertext unchanged** when it
  can't decrypt, to stay compatible with pre-encryption plaintext rows.
  After a `SECRET_KEY` change that means the app would send an encrypted
  blob as an SMTP password instead of failing loudly.
- **`/api/docs`, `/api/redoc` and `/api/openapi.json` are public.** They
  expose the API's shape, not its data.
- **There is no `SECURITY.md`** and no documented way to report a
  vulnerability privately, which a public AGPL repo should have.

## Running the audit yourself

```bash
pip install pip-audit
pip-audit --no-deps -r requirements.txt
```

`--no-deps` audits the pinned direct dependencies. Transitive packages
(`starlette` is pinned explicitly for the reason above, `ecdsa` comes in
via `python-jose`) need a resolved environment to be picked up.
