"""
Renders a single work session's attendee sheet: registered
participants with their parcel, expected hours, any task assigned to
them for this session, and a blank signature line -- for printing and
bringing to the actual work session, so the coordinator can confirm
attendance and hours on paper.

Shares its page chrome (header/footer/@page, "Page X of Y") with every
other PDF in this app via app/pdf_chrome.py -- see that module's
docstring for why. Like the meeting sign-in sheet
(app/meeting_signin_sheet.py) and unlike the announcement flyer
(app/print_publisher.py), this is a normal multi-page document (a big
session could have more attendees than fit on one page), not
constrained to a single page.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from weasyprint import HTML

from app.pdf_chrome import wrap_document, org_footer_html, OrgFooterContext

EXTRA_CSS = """
h1 { font-size: 15pt; margin-top: 0.4cm; margin-bottom: 0.1cm; color: #1f2937; }
.subtitle { font-size: 10pt; color: #4b5563; margin-bottom: 0.5cm; }
table { width: 100%; border-collapse: collapse; }
thead { display: table-header-group; } /* repeats on every page */
th { text-align: left; font-size: 8.5pt; text-transform: uppercase; color: #4b5563; border-bottom: 2px solid #2f6f3e; padding: 6px 6px; }
td { padding: 7px 6px; border-bottom: 1px solid #e5e7eb; vertical-align: top; }
td.parcel-col { font-weight: bold; white-space: nowrap; width: 2.4cm; }
td.member-col { width: 4.2cm; }
td.hours-col { width: 2cm; white-space: nowrap; }
td.tasks-col { width: 5cm; }
td.signature-col { border-bottom: 1px solid #9ca3af; }
"""


@dataclass
class AttendeeRow:
    parcel: str
    member_name: str
    hours: str
    tasks: str


def _body_html(headline: str, subtitle: str, rows: List[AttendeeRow]) -> str:
    rows_html = "".join(
        f'<tr><td class="parcel-col">{r.parcel}</td>'
        f'<td class="member-col">{r.member_name}</td>'
        f'<td class="hours-col">{r.hours}</td>'
        f'<td class="tasks-col">{r.tasks}</td>'
        f'<td class="signature-col"></td></tr>'
        for r in rows
    )

    return f"""
    <h1>{headline}</h1>
    <div class="subtitle">{subtitle}</div>
    <table>
        <thead>
            <tr>
                <th>Parcel</th>
                <th>Member</th>
                <th>Hours</th>
                <th>Tasks assigned</th>
                <th>Signature</th>
            </tr>
        </thead>
        <tbody>
            {rows_html}
        </tbody>
    </table>
    """


def render_session_attendee_sheet_pdf(
    headline: str, subtitle: str, footer_context: OrgFooterContext, logo_path: Optional[Path],
    rows: List[AttendeeRow], language: str = "en",
) -> bytes:
    """rows should already be sorted the way the caller wants them to
    appear -- this function doesn't re-sort."""
    html_doc = wrap_document(
        _body_html(headline, subtitle, rows),
        footer_context.club_name, logo_path, org_footer_html(footer_context, language), language,
        extra_css=EXTRA_CSS,
    )
    return HTML(string=html_doc).write_pdf()
