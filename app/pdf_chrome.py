"""
Shared PDF page chrome for every "formal document" PDF in this app:
invoices/reminders, the birthday calendar, the work-session attendee
sheet, and the meeting sign-in sheet all use this SAME visual
template -- a DIN-style fixed header (club logo at the true physical
page edge, club name centered across the full sheet), the same
color/font palette, a running footer, and localized "Page X of Y"
page numbering.

Deliberately the one shared source now, not independently duplicated
per generator: this app's invoice PDF originated the look, and each
later document (birthday calendar, then this refactor) was asked to
match it "from now on" -- see docs/ADR/0043 for the decision to
extract this rather than keep copy-pasting the same CSS block into
every new generator, which had been this codebase's pattern up to that
point.

NOT used by app/print_publisher.py (the announcement flyer): that one
is deliberately single-page with a different, centered header and no
page numbering (see its own module docstring) -- a physical notice
meant to be pinned up, not a multi-page administrative document, so it
was never meant to look like these.

Each document still supplies its own BODY content (table columns,
address blocks, whatever's specific to it) and its own footer content
-- invoices show a three-column bank/register/organization footer;
everything else here just shows the club name -- only the chrome
around that content is shared.
"""
from pathlib import Path
from typing import Optional

from app.i18n import translate
from app.pdf_utils import file_to_data_uri


def page_css(language: str, bottom_margin: str = "2.2cm", extra_css: str = "") -> str:
    """@page CSS with localized "Page X of Y" page numbering (baked
    into the CSS itself, not translated in the HTML body, since
    WeasyPrint only resolves page counters directly inside a margin
    box's own `content` property -- see app/invoice_pdf.py's original
    _page_css docstring for the full explanation, still accurate here).
    `bottom_margin` leaves room for a taller footer than the default
    one-line club-name footer (invoices' three-column bank/register
    footer needs more vertical space)."""
    page_word = translate("pdf.page_label", language)
    of_word = translate("pdf.of_label", language)
    return f"""
@page {{
    size: A4;
    margin: 2.2cm 1.5cm {bottom_margin} 1.5cm;
    @bottom-left {{ content: element(footer); width: 15.7cm; }}
    @bottom-right {{
        content: "{page_word} " counter(page) " {of_word} " counter(pages);
        font-size: 8pt; color: #6b7280;
    }}
}}
body {{ margin: 0; font-family: 'DejaVu Sans', sans-serif; color: #1f2937; font-size: 10.5pt; }}
/* True-page-edge header: the negative top/left offsets exactly cancel
   the @page margin above, so the logo sits at the physical page
   corner and the club name is centered across the full 21cm sheet,
   not just the printable area -- WeasyPrint's containing block for
   position:fixed here is the page's own content box (inset by the
   @page margin), not the raw sheet, so this cancels that inset back
   out to the true page corner. */
#header {{ position: fixed; top: -2.2cm; left: -1.5cm; width: 21cm; height: 1.8cm; border-bottom: 2px solid #2f6f3e; }}
#header .header-logo {{ position: absolute; left: 0.5cm; top: 0.3cm; }}
#header .header-logo img {{ max-height: 50px; }}
#header .club-name {{
    position: absolute; left: 0; top: 0.65cm; width: 21cm; text-align: center;
    font-size: 13pt; font-weight: bold; color: #2f6f3e; white-space: nowrap;
}}
#footer {{
    position: running(footer); font-size: 7.5pt; color: #6b7280;
    border-top: 1px solid #d1d5db; padding-top: 6px;
}}
{extra_css}
"""


def header_html(club_name: str, logo_path: Optional[Path]) -> str:
    logo_data_uri = file_to_data_uri(logo_path)
    logo_block = f'<img src="{logo_data_uri}">' if logo_data_uri else ""
    return f"""<div id="header">
        <div class="header-logo">{logo_block}</div>
        <div class="club-name">{club_name}</div>
    </div>"""


def wrap_document(
    body_html: str, club_name: str, logo_path: Optional[Path], footer_html: str,
    language: str, extra_css: str = "", bottom_margin: str = "2.2cm",
) -> str:
    """Wraps `body_html` in the shared page chrome (header/footer/@page
    rules); `extra_css` is appended after the shared CSS so a document
    can add its own rules (table columns, address blocks, ...) without
    a second <style> tag."""
    return f"""
    <html>
    <head><meta charset="utf-8"><style>{page_css(language, bottom_margin, extra_css)}</style></head>
    <body>
        {header_html(club_name, logo_path)}
        <div id="footer">{footer_html}</div>
        {body_html}
    </body>
    </html>
    """
