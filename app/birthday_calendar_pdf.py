"""
Renders a printable birthday calendar covering an entire year (issue
#99): every active member with a birth date on file, grouped by month
(Jan-Dec) in a single document -- unlike /calendar/birthdays' web view,
which only lists the next 90 days for the dashboard-style "upcoming"
use case (see app.birthdays.upcoming_birthdays vs birthdays_for_year).

Deliberately shares its visual template with app/invoice_pdf.py rather
than the plainer header style used by app/meeting_signin_sheet.py /
app/session_attendee_sheet.py: same DIN-style fixed header (logo at the
true page edge, club name centered across the full page), same
color/font palette, same footer treatment, and the same localized
"Page X of Y" page-numbering approach (a _page_css(language) function,
not a static CSS constant -- see invoice_pdf.py's own docstring on
_page_css for why the page-number text has to be baked into the CSS
itself rather than translated in the HTML body). Not extracted into a
shared helper module: this codebase's other PDF generators
(meeting_signin_sheet.py, session_attendee_sheet.py) already each own
their full CSS independently rather than sharing a chrome module, so
this follows that same established convention.
"""
from pathlib import Path
from typing import List, Optional

from babel.dates import get_month_names
from weasyprint import HTML

from app.birthdays import YearBirthdayEntry
from app.i18n import translate
from app.pdf_utils import file_to_data_uri


def _page_css(language: str) -> str:
    page_word = translate("calendar.birthdays.pdf_page_label", language)
    of_word = translate("calendar.birthdays.pdf_of_label", language)
    return f"""
@page {{
    size: A4;
    margin: 2.2cm 1.5cm 2.6cm 1.5cm;
    @bottom-left {{ content: element(footer); width: 15.7cm; }}
    @bottom-right {{
        content: "{page_word} " counter(page) " {of_word} " counter(pages);
        font-size: 8pt; color: #6b7280;
    }}
}}
body {{ margin: 0; font-family: 'DejaVu Sans', sans-serif; color: #1f2937; font-size: 10.5pt; }}
/* Same true-page-edge header treatment as app/invoice_pdf.py: the
   negative top/left offsets exactly cancel the @page margin above, so
   the logo sits at the physical page corner and the club name is
   centered across the full 21cm sheet, not just the printable area. */
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
h1 {{ font-size: 14pt; margin-top: 0.4cm; margin-bottom: 0.6cm; color: #1f2937; }}
table {{ width: 100%; border-collapse: collapse; }}
thead {{ display: table-header-group; }} /* repeats on every page */
th {{ text-align: left; font-size: 9pt; text-transform: uppercase; color: #4b5563; border-bottom: 2px solid #2f6f3e; padding: 6px 8px; }}
td {{ padding: 6px 8px; border-bottom: 1px solid #e5e7eb; vertical-align: top; }}
td.day-col {{ font-weight: bold; white-space: nowrap; width: 2.2cm; }}
td.age-col {{ white-space: nowrap; width: 3cm; }}
tr.month-row td {{ background: #eef6f0; font-weight: bold; color: #2f6f3e; border-top: 1px solid #2f6f3e; border-bottom: 1px solid #2f6f3e; padding-top: 8px; padding-bottom: 8px; }}
tr.round-birthday td {{ background: #fef3c7; }}
"""


def _build_html(
    heading: str, club_name: str, logo_data_uri: Optional[str],
    entries: List[YearBirthdayEntry], language: str, empty_text: str,
) -> str:
    logo_block = f'<img src="{logo_data_uri}">' if logo_data_uri else ""
    month_names = get_month_names("wide", context="stand-alone", locale=language)

    rows_html = []
    current_month: Optional[int] = None
    for entry in entries:
        if entry.month != current_month:
            current_month = entry.month
            rows_html.append(
                f'<tr class="month-row"><td colspan="3">{month_names[current_month]}</td></tr>'
            )
        row_class = "round-birthday" if entry.is_round else ""
        age_text = translate("calendar.birthdays.turning_label", language, age=entry.turning_age)
        if entry.is_round:
            age_text += f" ({translate('calendar.birthdays.round_badge', language)})"
        rows_html.append(
            f'<tr class="{row_class}">'
            f'<td class="day-col">{entry.day:02d}.</td>'
            f'<td>{entry.member.full_name}</td>'
            f'<td class="age-col">{age_text}</td>'
            f'</tr>'
        )

    if not rows_html:
        rows_html.append(f'<tr><td colspan="3" style="text-align: center; color: #6b7280;">{empty_text}</td></tr>')

    col_day = translate("calendar.birthdays.pdf_col_day", language)
    col_name = translate("calendar.birthdays.col_name", language)
    col_turning = translate("calendar.birthdays.col_turning", language)

    return f"""
    <html>
    <head><meta charset="utf-8"><style>{_page_css(language)}</style></head>
    <body>
        <div id="header">
            <div class="header-logo">{logo_block}</div>
            <div class="club-name">{club_name}</div>
        </div>
        <div id="footer">{club_name}</div>
        <h1>{heading}</h1>
        <table>
            <thead>
                <tr>
                    <th>{col_day}</th>
                    <th>{col_name}</th>
                    <th>{col_turning}</th>
                </tr>
            </thead>
            <tbody>
                {''.join(rows_html)}
            </tbody>
        </table>
    </body>
    </html>
    """


def render_birthday_calendar_pdf(
    year: int, club_name: str, logo_path: Optional[Path],
    entries: List[YearBirthdayEntry], language: str,
) -> bytes:
    """entries should already be sorted by (month, day) -- see
    app.birthdays.birthdays_for_year(); this function doesn't re-sort."""
    logo_data_uri = file_to_data_uri(logo_path)
    heading = translate("calendar.birthdays.pdf_heading", language, year=year)
    empty_text = translate("calendar.birthdays.pdf_empty", language)
    html_doc = _build_html(heading, club_name, logo_data_uri, entries, language, empty_text)
    return HTML(string=html_doc).write_pdf()
