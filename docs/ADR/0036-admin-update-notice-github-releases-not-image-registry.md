# Admin update notice: GitHub releases, not an image registry

**Context:** Admins have no way to know a newer Parcella version exists
short of watching the GitHub repo themselves. Added a background check
(`app/update_check.py`) that periodically compares the running
`app_version` (`app/config.py`) against the latest GitHub release for
`kermie/parcella`, caches the result in `ClubSettings`
(`update_check_latest_version`, `update_check_checked_at`), and shows a
notice with update instructions on `/admin/` when a newer release
exists. Toggle: `update_check_enabled` in Admin -> Settings (default
on) -- a "check now" button also exists for admins who don't want to
wait for the 6-hourly background loop.

**Checks GitHub releases, not a Docker registry, because there's no
image to check yet.** `docker-compose.yml`'s `web` service has no
`image:` key -- it `build`s from the local `Dockerfile`. Only `db`
(`postgres:16-alpine`) is a pullable image today. That means the
instructions this feature shows the admin (`cd` into the Docker folder,
`docker compose pull`, `docker compose up -d`) are only fully accurate
once `web:` is switched to reference a published, versioned image --
until then, `docker compose pull` refreshes Postgres but not the app
itself. Checking GitHub releases doesn't depend on that: releases/tags
are repo metadata, unrelated to whether a Docker image has been
published for them. So the check itself works today; only the
instructions it hands out assume a deployment shape (`web:` pointing at
a registry) that doesn't exist yet. When that changes, this feature
needs no rework -- just point `LATEST_RELEASE_URL` logic (if ever
needed) or leave it as is, since the release check was never coupled to
the registry in the first place.

**Cached, not live-checked on every dashboard load.** A background loop
(`_update_check_polling_loop` in `app/main.py`, same shape as the
existing ticket-inbox polling loop) refreshes the cache every 6 hours;
`/admin/` only ever reads the cache. Avoids an outbound call blocking a
page render, and avoids hitting GitHub's API on every admin page view.

**Version comparison is a plain dotted-integer tuple compare
(`is_newer()`), not a semver library.** `app_version` is currently
`"0.1.0"` with no pre-release/build-metadata suffixes in use anywhere
in this project -- adding a semver dependency for three-part version
tuples would be more machinery than the problem needs. Anything that
doesn't parse as dotted integers (on either side) is treated as "not
newer" rather than raising, so a malformed or missing GitHub tag fails
quiet rather than breaking the admin dashboard.

**No credentials, no mock-based tests for the live HTTP call.** The
GitHub releases-for-latest endpoint is public; nothing is sent besides
the plain GET. Same test-coverage shape as `app/spam_filter.py`'s
one-shot outbound call: the comparison/caching logic
(`is_newer`, `get_update_status`, the disabled-skips-the-call path) is
unit-tested, but `fetch_latest_release_version()` itself isn't
exercised against a mock in the test suite -- consistent with this
project's existing precedent for simple one-shot external calls, as
opposed to the WordPress/Nextcloud integrations (which inject a client
and use `httpx.MockTransport`, since those involve credentials and
multi-step flows worth locking down more tightly).
