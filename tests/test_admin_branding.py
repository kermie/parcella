"""
Issue #101: "I want to upload a new logo... presently this seems not
possible." The upload mechanism itself (app/branding.py's
save_logo_upload, the /admin/settings POST handler) was already fully
functional -- verified here with real round-trip tests, since none
existed before. The actual problem was discoverability: the file input
lived inside the generic "Club Data" accordion card, collapsed by
default, with no mention of "logo" in its header, so it was easy to
never notice it exists. Fixed by giving it its own clearly-labeled
"Branding" card (later changed to collapsed by default, like every
other settings card, so a fresh page load never expands anything).

Logo uploads always save under a FIXED filename (logo.<ext>) at the
real, git-tracked app/static/uploads/logo.png -- not a per-test mock
path -- since app/branding.py's UPLOAD_DIR is a plain relative path,
not routed through the test DB. Every test here backs up and restores
whatever logo.* file(s) already exist before/after, so running this
suite never permanently clobbers the real default logo on disk.
"""
from pathlib import Path

UPLOAD_DIR = Path("app/static/uploads")

_TINY_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _backup_existing_logo_files() -> dict:
    return {p: p.read_bytes() for p in UPLOAD_DIR.glob("logo.*")}


def _restore_logo_files(original: dict) -> None:
    for p in UPLOAD_DIR.glob("logo.*"):
        if p not in original:
            p.unlink()
    for p, data in original.items():
        p.write_bytes(data)


async def web_login(client, email: str, password: str = "testpasswort123") -> None:
    response = await client.post("/auth/login", data={"email": email, "password": password})
    assert response.status_code in (302, 303)


async def test_logo_upload_saves_file_and_setting(client, admin_user):
    await web_login(client, "admin@example.com")
    original = _backup_existing_logo_files()
    try:
        resp = await client.post(
            "/admin/settings", data={},
            files={"logo": ("mylogo.png", _TINY_PNG_BYTES, "image/png")},
        )
        assert resp.status_code in (302, 303)
        assert "logo_error" not in resp.headers.get("location", "")

        from app.database import AsyncSessionLocal
        from app.models import ClubSetting
        from sqlalchemy import select

        async with AsyncSessionLocal() as session:
            result = await session.execute(select(ClubSetting).where(ClubSetting.key == "logo_filename"))
            entry = result.scalar_one_or_none()
        assert entry is not None
        assert entry.value == "logo.png"
        assert (UPLOAD_DIR / "logo.png").read_bytes() == _TINY_PNG_BYTES

        page = await client.get("/admin/settings")
        assert "/static/uploads/logo.png" in page.text
    finally:
        _restore_logo_files(original)


async def test_logo_removal_clears_setting_and_file(client, admin_user):
    await web_login(client, "admin@example.com")
    original = _backup_existing_logo_files()
    try:
        upload = await client.post(
            "/admin/settings", data={},
            files={"logo": ("mylogo.png", _TINY_PNG_BYTES, "image/png")},
        )
        assert upload.status_code in (302, 303)
        assert (UPLOAD_DIR / "logo.png").exists()

        removal = await client.post("/admin/settings", data={"remove_logo": "true"})
        assert removal.status_code in (302, 303)

        from app.database import AsyncSessionLocal
        from app.models import ClubSetting
        from sqlalchemy import select

        async with AsyncSessionLocal() as session:
            result = await session.execute(select(ClubSetting).where(ClubSetting.key == "logo_filename"))
            entry = result.scalar_one_or_none()
        assert entry is None
        assert not (UPLOAD_DIR / "logo.png").exists()

        page = await client.get("/admin/settings")
        assert "/static/uploads/logo.png" not in page.text
    finally:
        _restore_logo_files(original)


async def test_invalid_logo_type_rejected_with_error(client, admin_user):
    await web_login(client, "admin@example.com")
    original = _backup_existing_logo_files()
    try:
        resp = await client.post(
            "/admin/settings", data={},
            files={"logo": ("notlogo.txt", b"not an image", "text/plain")},
        )
        assert resp.status_code in (302, 303)
        assert "logo_error=invalid_logo_type" in resp.headers.get("location", "")
        # Rejected upload must not have created any file.
        assert not (UPLOAD_DIR / "logo.txt").exists()
    finally:
        _restore_logo_files(original)


async def test_reuploaded_logo_gets_a_fresh_cache_busting_url(client, admin_user):
    """Follow-up bug found while investigating #101: the upload itself
    quietly succeeded server-side, but the logo always saves under the
    SAME fixed filename (logo.<ext>), and Starlette's StaticFiles sets
    no Cache-Control header -- so a browser that already cached the old
    image at that identical URL could keep showing it indefinitely
    after a re-upload, looking exactly like "the upload didn't work".
    The displayed <img src> must therefore change on every re-upload
    (a `?v=` suffix derived from the ClubSetting row's own updated_at),
    even though the underlying path is unchanged."""
    await web_login(client, "admin@example.com")
    original = _backup_existing_logo_files()
    try:
        first = await client.post(
            "/admin/settings", data={},
            files={"logo": ("first.png", _TINY_PNG_BYTES, "image/png")},
        )
        assert first.status_code in (302, 303)
        page_1 = await client.get("/admin/settings")
        src_1 = _logo_img_src(page_1.text)
        assert src_1 is not None
        assert "?v=" in src_1

        different_png = _TINY_PNG_BYTES + b"\x00"  # distinct content, still saved as logo.png
        second = await client.post(
            "/admin/settings", data={},
            files={"logo": ("second.png", different_png, "image/png")},
        )
        assert second.status_code in (302, 303)
        page_2 = await client.get("/admin/settings")
        src_2 = _logo_img_src(page_2.text)
        assert src_2 is not None

        assert src_1 != src_2, "re-uploading a logo must change its displayed URL, or a browser may keep showing the old cached image"
        assert src_1.split("?")[0] == src_2.split("?")[0] == "/static/uploads/logo.png"
    finally:
        _restore_logo_files(original)


def _logo_img_src(html: str) -> str:
    """Finds the logo's own <img src="..."> specifically (by matching
    "/static/uploads/logo" in the src value) rather than just the first
    <img> tag on the page -- base.html's sidebar also renders a logo
    <img>, so a page can legitimately contain more than one image tag."""
    marker_start = html.find('src="/static/uploads/logo')
    assert marker_start != -1, "expected a logo <img src=\"/static/uploads/logo...\"> once a logo is set"
    value_start = marker_start + len('src="')
    value_end = html.find('"', value_start)
    return html[value_start:value_end]


async def test_branding_card_is_collapsed_by_default_and_holds_the_only_logo_input(client, admin_user):
    """The fix for issue #101: logo upload gets its own always-visible
    card, and the old duplicate copy inside "Club Data" is gone (so
    there's exactly one `name="logo"` file input on the whole page).

    No settings card is expanded on a fresh page load (later request) --
    every card, including this one, starts collapsed."""
    await web_login(client, "admin@example.com")
    page = await client.get("/admin/settings")
    assert page.status_code == 200

    assert page.text.count('name="logo"') == 1, "the logo file input must not be duplicated across cards"

    # The Branding card must be collapsed on initial load -- find its
    # toggle/collapse pair and check both carry the "collapsed"/hidden
    # markers.
    branding_toggle_start = page.text.find('data-bs-target="#settings-card-branding"')
    assert branding_toggle_start != -1, "expected a dedicated #settings-card-branding toggle"
    toggle_tag_start = page.text.rfind("<div", 0, branding_toggle_start)
    toggle_tag_end = page.text.find(">", branding_toggle_start)
    toggle_tag = page.text[toggle_tag_start:toggle_tag_end]
    assert "collapsed" in toggle_tag
    assert 'aria-expanded="false"' in toggle_tag

    body_start = page.text.find('id="settings-card-branding"')
    body_tag_start = page.text.rfind("<div", 0, body_start)
    body_tag_end = page.text.find(">", body_start)
    body_tag = page.text[body_tag_start:body_tag_end]
    assert "collapse show" not in body_tag
