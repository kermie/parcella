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
- Falls back to "Gartenverein" and no logo (the default tree icon) if
  nothing has been configured yet, so existing installs aren't left
  with a blank name.
"""
import os
from pathlib import Path
from typing import Optional

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import ClubSetting

DEFAULT_CLUB_NAME = "Gartenverein"

UPLOAD_DIR = Path("app/static/uploads")
ALLOWED_LOGO_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
}
MAX_LOGO_SIZE_BYTES = 2 * 1024 * 1024  # 2 MB


async def load_branding(db: AsyncSession) -> dict:
    """Loads the club's display name and logo path from ClubSetting."""
    result = await db.execute(
        select(ClubSetting).where(ClubSetting.key.in_(["verein_name", "logo_filename"]))
    )
    values = {e.key: e.value for e in result.scalars().all()}
    club_name = values.get("verein_name") or DEFAULT_CLUB_NAME
    logo_filename = values.get("logo_filename")
    logo_url = f"/static/uploads/{logo_filename}" if logo_filename else None
    return {"club_name": club_name, "logo_url": logo_url}


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
