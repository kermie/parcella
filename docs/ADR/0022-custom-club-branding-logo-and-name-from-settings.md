# Custom club branding: logo and name from settings

**Context:** the software is meant for adoption by any allotment-garden
association, not just the one it was originally built for -- a
hardcoded "Gartenverein" name and a fixed tree icon in the sidebar
doesn't fit that goal.

**Decision:** two things, loaded once per request via a new middleware
(`app/branding.py`), following the exact same pattern already
established for language, module flags, and region/currency
(`request.state.club_name` / `request.state.logo_url`, available on
every page without any router needing to fetch them individually):
- The club's display name reuses the existing `verein_name` ClubSetting
  (already used for the address block) rather than introducing a
  second, redundant name field.
- A new `logo_filename` ClubSetting, paired with an actual uploaded
  image file under `app/static/uploads/logo.<ext>` -- validated
  (allowed image types, 2MB limit) and always saved under a fixed name
  so re-uploading a different file type cleanly replaces the old one
  rather than leaving it orphaned but still reachable.

Falls back to "Gartenverein" and the default tree icon when nothing's
configured, so a fresh install doesn't look broken before an admin sets
things up.

**Update (issue #101), part 1 -- discoverability:** the logo file
input originally lived inside the generic "Club Data" accordion card
on `/admin/settings` -- collapsed by default, with nothing in its
header hinting "logo" -- so an admin could reasonably never notice it
exists. Given its own "Branding" card, open by default like "Language"
above it, so scanning the section headers alone reveals it. Also the
first real test coverage for `save_logo_upload`/the settings-page
upload route (`tests/test_admin_branding.py`) -- there was none before.

**Update (issue #101), part 2 -- a genuine caching bug found while
verifying part 1:** even after fixing discoverability, a user reported
the upload *still* didn't visibly work. The server-side save was
actually succeeding (file written, `logo_filename` ClubSetting
updated) -- the problem was that `logo_url` is always the exact same
`/static/uploads/logo.<ext>` regardless of content, and Starlette's
`StaticFiles` sets `Last-Modified`/`ETag` but no `Cache-Control`, so a
browser that had already cached the old image at that URL could keep
showing it indefinitely after a re-upload, with nothing forcing a
revalidation. Fixed with a `logo_version` cache-busting query param
(`?v=...`), appended only where the logo is rendered as an `<img
src>` (`app/main.py`'s `request.state.logo_url`) -- deliberately
**not** baked into `load_branding()`'s own `logo_url`, since four
routers (announcements/finances/work_hours/members) build a filesystem
`Path` directly from `branding["logo_url"]` to embed the logo in a
PDF, and a query string there would break `Path.exists()` for all of
them.

The version value is the **logo file's own mtime on disk**, not the
`ClubSetting` row's `updated_at` -- tried `updated_at` first and it
looked right in isolation, but failed on the realistic case of
re-uploading a file with the *same* extension (by far the common
case): SQLAlchemy's flush skips emitting an `UPDATE` (and therefore
skips `onupdate=func.now()`) for a column whose newly-assigned value
equals what it already held, and `logo_filename` is always the
identical string `"logo.png"` for two PNG uploads in a row. The file
on disk, by contrast, is unconditionally rewritten every time
regardless of extension, so its mtime is the value that actually
changes on every real re-upload.

