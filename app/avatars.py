"""
User avatars (issue #150): an optional profile image per user, shown
next to their name everywhere a system user's name appears (sidebar
footer, admin user list/edit, task assignees/comments, etc).

Stored as app/static/uploads/avatars/<user_id>.<ext> -- one file per
user id, unlike the club logo (app/branding.py), which uses a single
fixed filename for the whole club. Re-uploading with a different image
type still has to remove the old extension's file first, same
_delete_existing_logo_files situation as branding.py, just scoped per
user here.

Deliberately raster-only (no SVG): the club logo allows SVG because
only a system admin can upload it, but any logged-in user can upload
their own avatar here, and an SVG can carry a <script> -- excluding it
avoids that surface entirely rather than trying to sanitize it.
"""
from pathlib import Path
from typing import Optional

from fastapi import UploadFile

AVATAR_UPLOAD_DIR = Path("app/static/uploads/avatars")
ALLOWED_AVATAR_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}
MAX_AVATAR_SIZE_BYTES = 2 * 1024 * 1024  # 2 MB


def _delete_existing_avatar_files(user_id: str) -> None:
    if not AVATAR_UPLOAD_DIR.exists():
        return
    for existing in AVATAR_UPLOAD_DIR.glob(f"{user_id}.*"):
        try:
            existing.unlink()
        except OSError:
            pass


async def save_avatar_upload(user_id: str, file: UploadFile) -> str:
    """Validates and saves an uploaded avatar image, returning the
    filename to store in User.avatar_filename. Raises ValueError with a
    translation-key message on anything invalid (wrong type, too large)."""
    if file.content_type not in ALLOWED_AVATAR_TYPES:
        raise ValueError("invalid_avatar_type")

    contents = await file.read()
    if len(contents) > MAX_AVATAR_SIZE_BYTES:
        raise ValueError("avatar_too_large")

    AVATAR_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    _delete_existing_avatar_files(user_id)

    extension = ALLOWED_AVATAR_TYPES[file.content_type]
    filename = f"{user_id}{extension}"
    with open(AVATAR_UPLOAD_DIR / filename, "wb") as f:
        f.write(contents)

    return filename


def remove_avatar_file(user_id: str) -> None:
    """Deletes any stored avatar file for this user (used when the user
    or an admin removes the avatar, reverting to the initials/icon
    fallback)."""
    _delete_existing_avatar_files(user_id)


def avatar_url(avatar_filename: Optional[str]) -> Optional[str]:
    """Builds the cache-busted <img src> URL for a stored avatar
    filename, or None if there's nothing to show (same mtime-based
    cache-busting rationale as app/branding.py's load_branding --
    the file always saves under the same fixed per-user name, so
    without a version suffix a browser could keep showing a stale
    cached image after a re-upload)."""
    if not avatar_filename:
        return None
    path = AVATAR_UPLOAD_DIR / avatar_filename
    if not path.exists():
        return None
    version = int(path.stat().st_mtime * 1_000_000)
    return f"/static/uploads/avatars/{avatar_filename}?v={version}"
