"""
Renders a general-meeting sign-in sheet: current members, grouped by
parcel number, each with a blank signature line -- for printing and
bringing to a physical meeting.

Unlike the announcement flyer (app.print_publisher), this is
deliberately NOT constrained to one page: a real member roster can run
to several pages, and unlike a flyer there's no "shorten it" option for
a list of people who need to sign in. Instead it's a normal multi-page
document with a repeating header/footer and "Page X of Y" numbering.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from weasyprint import HTML

from app.pdf_utils import file_to_data_uri

PAGE_CSS = """
@page {
    size: A4;
    margin: 2.2cm 1.5cm 2.2cm 1.5cm;
    @top-center { content: element(header); }
    @bottom-left { content: element(footer); }
    @bottom-right {
        content: "Page " counter(page) " of " counter(pages);
        font-size: 8pt; color: #6b7280;
    }
}
body { font-family: 'DejaVu Sans', sans-serif; color: #1f2937; font-size: 10.5pt; }
#header { position: running(header); text-align: center; border-bottom: 2px solid #2f6f3e; padding-bottom: 8px; }
#header img { max-height: 50px; margin-bottom: 4px; }
#header .club-name { font-size: 13pt; font-weight: bold; color: #2f6f3e; }
#footer { position: running(footer); font-size: 8pt; color: #6b7280; border-top: 1px solid #d1d5db; padding-top: 6px; }
h1 { font-size: 15pt; margin-top: 0.4cm; margin-bottom: 0.6cm; color: #1f2937; }
table { width: 100%; border-collapse: collapse; }
thead { display: table-header-group; } /* repeats on every page */
th { text-align: left; font-size: 9pt; text-transform: uppercase; color: #4b5563; border-bottom: 2px solid #2f6f3e; padding: 6px 8px; }
td { padding: 7px 8px; border-bottom: 1px solid #e5e7eb; vertical-align: top; }
td.parcel-col { font-weight: bold; white-space: nowrap; width: 3.2cm; border-right: 1px solid #e5e7eb; }
td.name-col { width: 6.5cm; }
td.signature-col { border-bottom: 1px solid #9ca3af; }
tr.parcel-group-start td { border-top: 1px solid #d1d5db; }
"""


@dataclass
class ParcelGroup:
    plot_number: str
    member_names: List[str]


def _build_html(headline: str, club_name: str, logo_data_uri: Optional[str], groups: List[ParcelGroup]) -> str:
    logo_block = f'<img src="{logo_data_uri}">' if logo_data_uri else ""

    rows_html = []
    for group in groups:
        for row_index, name in enumerate(group.member_names):
            is_first_row_in_group = row_index == 0
            row_class = "parcel-group-start" if is_first_row_in_group else ""
            parcel_cell = (
                f'<td class="parcel-col" rowspan="{len(group.member_names)}">{group.plot_number}</td>'
                if is_first_row_in_group else ""
            )
            rows_html.append(
                f'<tr class="{row_class}">{parcel_cell}'
                f'<td class="name-col">{name}</td>'
                f'<td class="signature-col"></td></tr>'
            )

    return f"""
    <html>
    <head><meta charset="utf-8"><style>{PAGE_CSS}</style></head>
    <body>
        <div id="header">
            {logo_block}
            <div class="club-name">{club_name}</div>
        </div>
        <div id="footer">{club_name}</div>
        <h1>{headline}</h1>
        <table>
            <thead>
                <tr>
                    <th>Parcel</th>
                    <th>Name</th>
                    <th>Signature</th>
                </tr>
            </thead>
            <tbody>
                {''.join(rows_html)}
            </tbody>
        </table>
    </body>
    </html>
    """


def render_meeting_signin_sheet_pdf(
    headline: str, club_name: str, logo_path: Optional[Path],
    parcel_members: List[Tuple[str, List[str]]],
) -> bytes:
    """parcel_members: list of (plot_number, [member full names]),
    already sorted the way the caller wants them to appear -- this
    function doesn't re-sort, so grouping order is entirely the
    caller's responsibility."""
    logo_data_uri = file_to_data_uri(logo_path, "image/png")
    groups = [ParcelGroup(plot_number=p, member_names=names) for p, names in parcel_members]
    html_doc = _build_html(headline, club_name, logo_data_uri, groups)
    return HTML(string=html_doc).write_pdf()
