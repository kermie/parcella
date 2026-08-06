# Repo transferred from kermie/parcella to parcella-garden/parcella

**Context:** the repo moved from a personal account (`kermie`) to a
dedicated GitHub org (`parcella-garden`) via GitHub's own transfer
feature. This is a one-time housekeeping note, not a design decision,
but it changes several places that hardcoded the old owner and would
otherwise silently break.

**`.github/workflows/release.yml` now derives the image tag from
`${{ github.repository }}` instead of hardcoding `kermie/parcella`.**
The publish job authenticates with the repo's own `GITHUB_TOKEN`
(ADR 0068), which only has push rights into the GHCR namespace of
whichever owner the workflow is currently running under. A hardcoded
`ghcr.io/kermie/parcella` tag would have started failing publish jobs
the moment the repo moved, since the org's token can't push into a
personal account's package namespace. Deriving the tag from
`github.repository` means a future transfer (or a fork) doesn't need
this file touched again.

**`app/update_check.py`'s `GITHUB_REPO` constant, `docker-compose.prod.yml`'s
default image, and the doc links in README/CONTRIBUTING/SECURITY were
updated to `parcella-garden/parcella`.** These aren't dynamic like the
workflow tag -- `update_check.py` calls the GitHub Releases API from a
running server process with no `github.repository` context available --
so they're plain hardcoded strings that just needed the find-and-replace.

**Historical issue links inside older ADRs (0060-0063) were left
pointing at `kermie/parcella/issues/...`.** GitHub's transfer redirect
keeps old-owner URLs resolving indefinitely (unless someone later
registers a new repo at the vacated `kermie/parcella` slug), and these
are citations of a specific past state, not statements of the repo's
current identity -- rewriting them would be churn for no benefit.

**The old GHCR package (`ghcr.io/kermie/parcella`, all versions
published before the transfer) is not migrated.** Container packages
published via `GITHUB_TOKEN` belong to the account/org that owned the
repo at publish time and don't move with a later repo transfer. Old
release tags keep resolving under the old path; every release from now
on publishes under `ghcr.io/parcella-garden/parcella` per the workflow
change above. No redirect exists between the two package namespaces --
anyone pinned to a pre-transfer tag needs to know the old path still
works, but `latest` only continues to update at the new one.
