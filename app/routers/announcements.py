"""
Announcements module router.

Foundation piece only (see docs/module-announcements.md): authoring an
Announcement (title, Markdown body, optional image, optional print
override) and its lifecycle (draft/published/archived). Sending it out
to the three channels (blog draft, email, PDF) is built on top of this
in later phases -- this router does not yet send anything anywhere.

Restricted to board/admin (require_admin), same reasoning as the
public_signup_api's Integrations page: this creates content that will
be pushed to a public blog and to every member's inbox, so authoring
it isn't a general member-facing feature.
"""
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request, Depends, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import Announcement, AnnouncementStatus
from app.auth import require_admin
from app.module_flags import require_modul
from app.announcement_utils import render_markdown_to_html, likely_fits_one_print_page
from app.templating import templates

router = APIRouter(
    prefix="/announcements",
    tags=["announcements"],
    dependencies=[Depends(require_modul("announcements"))],
)

UPLOAD_DIR = Path("app/static/uploads/announcements")
ALLOWED_IMAGE_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}
MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB


async def _save_announcement_image(file: UploadFile) -> str:
    """Validates and saves an uploaded announcement image, returning the
    filename to store on the Announcement. Unlike the singleton club
    logo (app/branding.py), each announcement can have its own image, so
    filenames are unique (uuid-based) rather than fixed."""
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise ValueError("invalid_image_type")

    contents = await file.read()
    if len(contents) > MAX_IMAGE_SIZE_BYTES:
        raise ValueError("image_too_large")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    extension = ALLOWED_IMAGE_TYPES[file.content_type]
    filename = f"{uuid.uuid4()}{extension}"
    with open(UPLOAD_DIR / filename, "wb") as f:
        f.write(contents)
    return filename


def _delete_announcement_image(filename: Optional[str]) -> None:
    if not filename:
        return
    path = UPLOAD_DIR / filename
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass


async def _get_announcement_or_404(db: AsyncSession, announcement_id: str) -> Announcement:
    result = await db.execute(select(Announcement).where(Announcement.id == announcement_id))
    announcement = result.scalar_one_or_none()
    if announcement is None:
        raise HTTPException(status_code=404, detail="Announcement not found")
    return announcement


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
async def announcement_list(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_admin(request, db)
    result = await db.execute(select(Announcement).order_by(Announcement.created_at.desc()))
    announcements = result.scalars().all()
    return templates.TemplateResponse("announcements/list.html", {
        "request": request, "user": user, "announcements": announcements,
    })


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

@router.get("/new", response_class=HTMLResponse)
async def announcement_new_form(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_admin(request, db)
    return templates.TemplateResponse("announcements/form.html", {
        "request": request, "user": user, "announcement": None, "error": None,
    })


@router.post("/new")
async def announcement_create(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await require_admin(request, db)
    form = await request.form()

    title = (form.get("title") or "").strip()
    body_markdown = (form.get("body_markdown") or "").strip()
    image_upload = form.get("image")

    if not title or not body_markdown:
        return templates.TemplateResponse("announcements/form.html", {
            "request": request, "user": user, "announcement": None,
            "error": "missing_fields",
        }, status_code=400)

    image_filename = None
    if image_upload is not None and getattr(image_upload, "filename", ""):
        try:
            image_filename = await _save_announcement_image(image_upload)
        except ValueError as e:
            return templates.TemplateResponse("announcements/form.html", {
                "request": request, "user": user, "announcement": None,
                "error": str(e),
            }, status_code=400)

    announcement = Announcement(
        title=title,
        body_markdown=body_markdown,
        body_html=render_markdown_to_html(body_markdown),
        image_filename=image_filename,
        status=AnnouncementStatus.DRAFT,
        created_by_id=user.id,
    )
    db.add(announcement)
    await db.commit()

    return RedirectResponse(url=f"/announcements/{announcement.id}/edit", status_code=303)


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------

@router.get("/{announcement_id}/edit", response_class=HTMLResponse)
async def announcement_edit_form(
    announcement_id: str, request: Request, db: AsyncSession = Depends(get_db),
):
    user = await require_admin(request, db)
    announcement = await _get_announcement_or_404(db, announcement_id)
    fits_one_page = likely_fits_one_print_page(
        announcement.print_text_override or announcement.body_markdown
    )
    return templates.TemplateResponse("announcements/form.html", {
        "request": request, "user": user, "announcement": announcement,
        "fits_one_page": fits_one_page, "error": None,
    })


@router.post("/{announcement_id}/edit")
async def announcement_update(
    announcement_id: str, request: Request, db: AsyncSession = Depends(get_db),
):
    user = await require_admin(request, db)
    announcement = await _get_announcement_or_404(db, announcement_id)
    form = await request.form()

    title = (form.get("title") or "").strip()
    body_markdown = (form.get("body_markdown") or "").strip()
    print_text_override = (form.get("print_text_override") or "").strip() or None
    remove_image = form.get("remove_image", "") == "true"
    image_upload = form.get("image")

    if not title or not body_markdown:
        return templates.TemplateResponse("announcements/form.html", {
            "request": request, "user": user, "announcement": announcement,
            "error": "missing_fields",
        }, status_code=400)

    if remove_image:
        _delete_announcement_image(announcement.image_filename)
        announcement.image_filename = None
    elif image_upload is not None and getattr(image_upload, "filename", ""):
        try:
            new_filename = await _save_announcement_image(image_upload)
            _delete_announcement_image(announcement.image_filename)
            announcement.image_filename = new_filename
        except ValueError as e:
            return templates.TemplateResponse("announcements/form.html", {
                "request": request, "user": user, "announcement": announcement,
                "error": str(e),
            }, status_code=400)

    announcement.title = title
    announcement.body_markdown = body_markdown
    announcement.body_html = render_markdown_to_html(body_markdown)
    announcement.print_text_override = print_text_override

    await db.commit()
    return RedirectResponse(url=f"/announcements/{announcement.id}/edit", status_code=303)


# ---------------------------------------------------------------------------
# Status changes / delete
# ---------------------------------------------------------------------------

@router.post("/{announcement_id}/archive")
async def announcement_archive(
    announcement_id: str, request: Request, db: AsyncSession = Depends(get_db),
):
    await require_admin(request, db)
    announcement = await _get_announcement_or_404(db, announcement_id)
    announcement.status = AnnouncementStatus.ARCHIVED
    await db.commit()
    return RedirectResponse(url="/announcements/", status_code=303)


@router.post("/{announcement_id}/delete")
async def announcement_delete(
    announcement_id: str, request: Request, db: AsyncSession = Depends(get_db),
):
    await require_admin(request, db)
    announcement = await _get_announcement_or_404(db, announcement_id)
    _delete_announcement_image(announcement.image_filename)
    await db.delete(announcement)
    await db.commit()
    return RedirectResponse(url="/announcements/", status_code=303)
