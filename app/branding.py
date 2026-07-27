"""
Club branding: custom logo and display name, shown in the sidebar and
page title on every page.

Concept:
- Two ClubSettings: "verein_name" (already existed -- the club's
  official name, also used for the address block elsewhere) and
  "logo_filename" (new -- just the filename of an uploaded logo image
  under app/static/uploads/, not the image itself).
- Loaded once per request in a middleware, same pattern as module flags
  (app/module_flags.py) and language (app/i18n.py), and stored under
  request.state.club_name / request.state.logo_url.
- Falls back to DEFAULT_CLUB_NAME and no logo (the default tree icon)
  if nothing has been configured yet, so existing installs aren't left
  with a blank name.
"""
import os
from pathlib import Path
from typing import Optional

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import ClubSetting

DEFAULT_CLUB_NAME = "Parcella"

UPLOAD_DIR = Path("app/static/uploads")
ALLOWED_LOGO_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
}
MAX_LOGO_SIZE_BYTES = 2 * 1024 * 1024  # 2 MB


async def load_branding(db: AsyncSession) -> dict:
    """Loads the club's display name and logo path from ClubSetting.

    `logo_version` is the logo *file's own mtime on disk* (issue #101
    follow-up), exposed separately from the plain `logo_url` path: the
    file always saves under the SAME fixed name (logo.<ext>), so
    without a cache-busting suffix somewhere, a browser that already
    cached the old image at that identical URL can keep showing it
    indefinitely after a re-upload -- Starlette's StaticFiles sets
    Last-Modified/ETag but no Cache-Control, so nothing forces a
    revalidation.

    Deliberately the file's mtime, NOT the ClubSetting row's own
    `updated_at`: re-uploading a same-extension file (e.g. PNG again)
    sets `logo_filename` to the identical string it already held
    ("logo.png"), and SQLAlchemy's flush skips emitting an UPDATE (and
    therefore skips onupdate=func.now()) for a column whose new value
    equals the old one -- so `updated_at` would silently never advance
    on the overwhelmingly common case of re-uploading the same file
    type, defeating the cache-busting entirely. The file on disk, by
    contrast, is unconditionally rewritten by save_logo_upload() every
    time regardless of extension, so its mtime always changes.

    Kept OUT of `logo_url` itself because four different routers
    (announcements/finances/work_hours/members) build a filesystem
    Path directly from `branding["logo_url"]` to embed the logo in a
    PDF -- appending a query string there would break `Path.exists()`
    for all of them. Callers that render an `<img src>` (see
    app/main.py's middleware) append `?v={logo_version}` themselves
    instead."""
    result = await db.execute(
        select(ClubSetting).where(ClubSetting.key.in_(["verein_name", "logo_filename"]))
    )
    entries = {e.key: e for e in result.scalars().all()}
    club_name = (entries["verein_name"].value if "verein_name" in entries else None) or DEFAULT_CLUB_NAME
    logo_entry = entries.get("logo_filename")
    logo_url = f"/static/uploads/{logo_entry.value}" if logo_entry and logo_entry.value else None
    logo_version = None
    if logo_entry and logo_entry.value:
        logo_path = UPLOAD_DIR / logo_entry.value
        if logo_path.exists():
            logo_version = int(logo_path.stat().st_mtime * 1_000_000)
    return {"club_name": club_name, "logo_url": logo_url, "logo_version": logo_version}


def _delete_existing_logo_files() -> None:
    """Removes any previously uploaded logo.* file so re-uploading with a
    different image type doesn't leave the old one orphaned but still
    reachable at its old URL."""
    if not UPLOAD_DIR.exists():
        return
    for existing in UPLOAD_DIR.glob("logo.*"):
        try:
            existing.unlink()
        except OSError:
            pass


async def save_logo_upload(file: UploadFile) -> str:
    """
    Validates and saves an uploaded logo image, returning the filename to
    store in ClubSetting. Raises ValueError with a user-facing message on
    anything invalid (wrong type, too large).
    """
    if file.content_type not in ALLOWED_LOGO_TYPES:
        raise ValueError("invalid_logo_type")

    contents = await file.read()
    if len(contents) > MAX_LOGO_SIZE_BYTES:
        raise ValueError("logo_too_large")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    _delete_existing_logo_files()

    extension = ALLOWED_LOGO_TYPES[file.content_type]
    filename = f"logo{extension}"
    with open(UPLOAD_DIR / filename, "wb") as f:
        f.write(contents)

    return filename


def remove_logo_file() -> None:
    """Deletes any stored logo file (used when the admin removes the logo,
    reverting to the default tree icon)."""
    _delete_existing_logo_files()
