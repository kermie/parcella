# Credential hardening: default SECRET_KEY refuses to boot, logins are throttled, the bootstrap admin must change its password

**Context:** The same security review found that the most likely way to
lose a Parcella installation was not an exotic bug but the shipped
defaults. All three of these were documented correctly in the README and
enforced by nothing:

* `SECRET_KEY` defaulted to `dev-secret-key-change-in-production`, and
  `docker-compose.yml` passed that same literal as its fallback. That
  key signs session cookies, signs API JWTs, and derives the Fernet key
  for stored SMTP/Nextcloud/WordPress passwords -- and it is published in
  a public AGPL repository.
* The first-start bootstrap creates `admin@parcella.local` with the
  password `admin1234`, identical on every installation, valid forever.
* Neither login endpoint had any rate limit, while the public signup API
  did.

**Decision 1: refuse to start on the default `SECRET_KEY` unless
`ENVIRONMENT=development`.** A pydantic validator in `app/config.py`
raises with the exact command to generate a real key. Chosen over the
two obvious alternatives: logging a warning (the class of operator who
misses the README also misses a log line at boot) and auto-generating a
key on first start (silently invalidates every session on restart, and
silently makes already-encrypted settings unreadable -- see ADR 0006).
The check keys off `is_development`, so nothing changes for local work
or for the test suite.

**Decision 2: only FAILED logins count toward the throttle, and both
entry points share the counters.** `app/login_throttle.py` limits per
IP+email (10 per 15 minutes) and per IP (30 per 15 minutes), on top of
`app/rate_limit.py` -- which is the public signup API's limiter,
extracted so there is one implementation rather than two.

There is deliberately **no per-email-only limit**: it would let anyone
lock a known board address out of their own account from any IP,
turning the protection into a denial-of-service tool. The accepted
consequence is that a distributed attack on one account is only slowed
by the per-IP-per-account limit; the right answer to that is fail2ban
at the reverse proxy, not a larger in-memory table here.

A successful login clears the counters, so a member who mistypes twice
isn't left one attempt from a lockout. The throttle deliberately still
applies when the *correct* password finally arrives -- otherwise an
attacker's last, successful guess would be the one request that sails
through.

**Decision 3: the bootstrap admin is locked until it changes its
password** (`User.must_change_password`, migration 0076). While the flag
is set, the web UI redirects every page to `/auth/change-password`
(`password_change_middleware`) and the REST API refuses to issue a token
at all -- otherwise the forced change would be a web-only gate any API
client could walk straight past with the documented default
credentials. Changing the password clears the flag; the change form
additionally refuses to "change" the password to the same value.

Only the bootstrap account gets the flag. Existing installations are not
affected by the migration (the column defaults to false for every
existing row), so an upgrade can never lock a club out of its own
instance -- which would be a worse outcome than the risk being fixed.

**Consequences:**

* `docker compose up` with no `.env` still works, because that path is
  `ENVIRONMENT=development`. A club that sets `ENVIRONMENT=production`
  without a key gets a startup error naming the fix, not a running
  instance with forgeable admin sessions.
* The README's documented first login now ends on the change-password
  screen rather than the dashboard. That is the point.
* Rate limiting is in-memory and per-process: it resets on restart and
  isn't shared across workers. It is a deterrent layered on top of the
  password check, never a replacement -- and behind a reverse proxy the
  per-IP limit only distinguishes callers if uvicorn runs with
  `--proxy-headers` and the proxy sets `X-Forwarded-For` (see
  docs/operations.md).
