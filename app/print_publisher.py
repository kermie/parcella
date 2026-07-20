"""
Print channel for the announcements module: renders a one-page,
branded PDF notice from an Announcement, meant for physical posting
on the allotment grounds.

Renders once with the full text (the manual print_text_override if the
admin set one, otherwise the full body). If that fits on one page,
done. If not, shortens paragraph-by-paragraph and re-renders each
attempt, stopping at the first one that fits -- adding a "read the
rest online" note with a QR code, but only once shortening actually
happened (untouched text never gets a QR code slapped on it) and only
if the WordPress draft has genuinely been published (see
app.blog_publisher.WordPressPublisher.get_public_url_if_published --
there's nothing public to point a QR code at until then, so the note
is simply omitted, not shown with a broken link).

If even a single paragraph still doesn't fit alongside the header,
footer, and image, generation stops and raises PrintTooLongError
rather than silently producing a multi-page "one-pager" or truncating
mid-sentence -- per the original design decision to ask a human in
that case.

The shortened text, once found, is written back onto
announcement.print_text_override so it's visible and freely editable
afterward, and so regenerating the PDF later doesn't have to redo the
same search.
"""
import base64
import io
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import qrcode
from weasyprint import HTML

from app.models import Announcement
from app.announcement_utils import render_markdown_to_html
from app.pdf_utils import file_to_data_uri

# A4, single page, with running header/footer boxes -- see the @page
# rule below. Since the whole point of this channel is "fits on one
# page", there's no need for WeasyPrint's more elaborate multi-page
# running-header machinery beyond what @top-center/@bottom-center
# already gives for free.
PAGE_CSS = """
@page {
    size: A4;
    margin: 2cm 1.5cm 2.2cm 1.5cm;
    @top-center { content: element(header); }
    @bottom-center { content: element(footer); }
}
body { font-family: 'DejaVu Sans', sans-serif; color: #1f2937; font-size: 11pt; line-height: 1.45; }
#header { position: running(header); text-align: center; border-bottom: 2px solid #2f6f3e; padding-bottom: 8px; }
#header img { max-height: 60px; margin-bottom: 4px; }
#header .club-name { font-size: 14pt; font-weight: bold; color: #2f6f3e; }
#footer { position: running(footer); text-align: center; font-size: 8pt; color: #6b7280; border-top: 1px solid #d1d5db; padding-top: 6px; }
h1 { font-size: 18pt; margin-top: 0.6cm; margin-bottom: 0.4cm; color: #1f2937; }
.announcement-image { max-width: 100%; max-height: 7cm; display: block; margin: 0.3cm auto; }
.online-note { margin-top: 0.6cm; padding-top: 0.3cm; border-top: 1px dashed #9ca3af; font-size: 9pt; color: #4b5563; display: flex; align-items: center; gap: 0.4cm; }
.online-note img { width: 2.2cm; height: 2.2cm; }
"""


class PrintTooLongError(Exception):
    """Raised when even the shortest attempt (a single paragraph)
    still doesn't fit on one printed page. The router turns this into
    a FAILED AnnouncementDelivery asking a human to shorten the text
    manually, rather than silently producing a multi-page flyer."""


@dataclass
class PrintRenderResult:
    pdf_bytes: bytes
    was_shortened: bool
    qr_included: bool


def _qr_data_uri(url: str) -> str:
    img = qrcode.make(url)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _split_paragraphs(markdown_text: str) -> List[str]:
    # Blank-line-separated paragraphs, the same convention Markdown
    # itself uses to tell paragraphs apart.
    return [p.strip() for p in markdown_text.split("\n\n") if p.strip()]


def _build_html(
    title: str, body_html: str, image_data_uri: Optional[str],
    logo_data_uri: Optional[str], club_name: str, online_note_html: str,
) -> str:
    image_block = f'<img class="announcement-image" src="{image_data_uri}">' if image_data_uri else ""
    logo_block = f'<img src="{logo_data_uri}">' if logo_data_uri else ""
    return f"""
    <html>
    <head><meta charset="utf-8"><style>{PAGE_CSS}</style></head>
    <body>
        <div id="header">
            {logo_block}
            <div class="club-name">{club_name}</div>
        </div>
        <div id="footer">{club_name}</div>
        <h1>{title}</h1>
        {image_block}
        <div>{body_html}</div>
        {online_note_html}
    </body>
    </html>
    """


def render_announcement_print_pdf(
    announcement: Announcement, club_name: str,
    logo_path: Optional[Path], image_path: Optional[Path],
    public_blog_url: Optional[str],
) -> PrintRenderResult:
    """Renders the announcement as a one-page PDF, shortening the text
    if needed. Mutates announcement.print_text_override if shortening
    happens (caller is responsible for persisting/committing that).
    Raises PrintTooLongError if it still doesn't fit even at the
    shortest attempt -- callers should not catch this to try again
    with different parameters; it means a human needs to shorten the
    source text."""
    logo_data_uri = file_to_data_uri(logo_path, "image/png")
    image_data_uri = None
    if image_path is not None and image_path.exists():
        image_data_uri = file_to_data_uri(image_path)

    def render(body_markdown: str, include_online_note: bool):
        online_note_html = ""
        if include_online_note and public_blog_url:
            qr_uri = _qr_data_uri(public_blog_url)
            online_note_html = (
                f'<div class="online-note"><img src="{qr_uri}">'
                f'<div>Read the full announcement online:<br>{public_blog_url}</div></div>'
            )
        html_doc = _build_html(
            announcement.title, render_markdown_to_html(body_markdown),
            image_data_uri, logo_data_uri, club_name, online_note_html,
        )
        return HTML(string=html_doc).render()

    source_markdown = announcement.print_text_override or announcement.body_markdown

    doc = render(source_markdown, include_online_note=False)
    if len(doc.pages) == 1:
        return PrintRenderResult(pdf_bytes=doc.write_pdf(), was_shortened=False, qr_included=False)

    # Doesn't fit as-is -- shorten paragraph by paragraph, most content
    # kept first, until it fits or we run out of paragraphs to drop.
    paragraphs = _split_paragraphs(source_markdown)
    for keep_count in range(len(paragraphs) - 1, 0, -1):
        shortened_markdown = "\n\n".join(paragraphs[:keep_count])
        doc = render(shortened_markdown, include_online_note=True)
        if len(doc.pages) == 1:
            # Persisted so it's visible and editable afterward, and so
            # the next generation doesn't repeat this search -- see
            # Announcement.print_text_override's docstring in models.py.
            announcement.print_text_override = shortened_markdown
            return PrintRenderResult(
                pdf_bytes=doc.write_pdf(), was_shortened=True, qr_included=bool(public_blog_url),
            )

    raise PrintTooLongError(
        "Even a single paragraph doesn't fit on one printed page alongside the header, footer, and image. "
        "Please shorten the text manually in the print override field and try again."
    )
