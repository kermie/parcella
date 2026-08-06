# Publish the web image to GHCR, split dev/prod docker-compose files

**Context:** ADR 0036 deliberately left one thing unfinished: the admin
update-check notice tells admins to `docker compose pull && docker
compose up -d`, but `docker-compose.yml`'s `web` service only ever
`build`s from the local `Dockerfile` -- there was no published image for
`pull` to actually fetch. That gap was accepted "for now," to be revisited
once there was an actual production go-live. This closes it.

**Registry: GHCR, not Docker Hub.** `ghcr.io/parcella-garden/parcella` needs no new
account and no new secret -- the CI workflow authenticates with the
`GITHUB_TOKEN` GitHub Actions already provides, and it's tied to the repo
admins already trust. Docker Hub would add a second account and a stored
access-token secret for no real benefit here.

**A separate `docker-compose.prod.yml`, not a modified `docker-compose.yml`.**
`docker-compose.yml` stays exactly the contributor/dev flow it already is
(`build:`, bind-mounted `./app` `./migrations` etc., `--reload`) --
unchanged, so `CLAUDE.md`, `CONTRIBUTING.md`, and `docs/operations.md`'s
existing dev-loop instructions stay accurate with no rewrite. The actual
target audience for a published image is a garden-club admin who
shouldn't need to clone the whole repo and build a container just to run
it -- `docker-compose.prod.yml` is the two-file (`docker-compose.prod.yml`
+ `.env`) download described in the README's "Production" section. The
alternative considered -- one file, with `build:` layered in for
contributors via a `docker-compose.override.yml` -- was rejected because
that override-file convention isn't used anywhere else in this repo, and
would still require every doc that currently says "run `docker compose
build web`" to change.

**Version sync: CI guard on tag push, not a build-time derivation.**
`app/config.py`'s `app_version` and the git tag that triggers a release
must agree (in fact they already didn't -- `app_version` was `"0.1.0"`
while the `v1.0.0` tag existed, a real instance of this exact problem).
The publish workflow (`.github/workflows/release.yml`) strips the `v` off
`github.ref_name` and fails the job outright if it doesn't match
`app_version`, rather than silently publishing a mismatched image.
Considered and rejected: deriving `app_version` automatically from the git
tag at build time via a Docker build ARG baked into an environment
override. That removes the manual-bump step entirely, but adds an
env-override code path to `app/config.py` that doesn't exist today, for a
project with a single maintainer cutting releases by hand -- more
machinery than the problem needs right now, consistent with this
project's version-comparison choice in ADR 0036 (plain dotted-integer
compare over a semver dependency, for the same reason).

**CI runs the existing test suite as a publish gate.** `.github/workflows/release.yml`'s
`test` job runs `./run_tests.sh` unmodified -- GitHub-hosted runners
already have Docker and the compose plugin, so the disposable `db_test`
container flow (see `CLAUDE.md`) works without any CI-specific test setup.
The `publish` job only runs if `test` passes.

**GHCR package visibility is a one-time manual step, not something this
ADR's workflow sets.** A package published via `GITHUB_TOKEN` defaults to
private even from a public repo's workflow. After the first successful
publish, the package needs to be manually switched to public (GitHub →
repo → Packages → `parcella` → visibility) before `docker compose pull`
works for anyone without registry credentials. Not automated here since
it's a one-time setting, not a recurring release step.

**No CHANGELOG.md added.** GitHub's own auto-generated release notes
(from commit history between tags) are enough for a single-maintainer
project at this stage; a hand-maintained changelog is something to add if
and when that stops being true, not preemptively.
