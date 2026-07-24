"""
Renders a single annual invoice as a PDF (issue #57) -- same WeasyPrint
approach as app/meeting_signin_sheet.py and app/session_attendee_sheet.py
(raw HTML string, @page running header/footer, "Page X of Y"), so it
looks consistent with the rest of the app's printed output.

render_invoice_pdf() takes plain values rather than an Invoice ORM
object so it works identically for a real, numbered, persisted invoice
and for a not-yet-persisted preview (see app/invoice_generation.py's
compute_invoices_for_run) -- the router builds an InvoicePdfData from
whichever source it has.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import List, Optional

from weasyprint import HTML

from app.pdf_utils import file_to_data_uri
from app.l10n import format_money

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
.meta-block { display: flex; justify-content: space-between; margin-top: 0.8cm; margin-bottom: 0.8cm; }
.recipient { white-space: pre-line; line-height: 1.5; }
.invoice-meta td { padding: 1px 6px; }
.invoice-meta td:first-child { color: #6b7280; }
.invoice-meta td:last-child { font-weight: bold; text-align: right; }
h1 { font-size: 14pt; margin-bottom: 0.1cm; color: #1f2937; }
.parcel-line { color: #6b7280; margin-bottom: 0.5cm; font-size: 9.5pt; }
table.items { width: 100%; border-collapse: collapse; margin-top: 0.3cm; }
table.items th { text-align: left; font-size: 9pt; text-transform: uppercase; color: #4b5563; border-bottom: 2px solid #2f6f3e; padding: 6px 8px; }
table.items td { padding: 7px 8px; border-bottom: 1px solid #e5e7eb; vertical-align: top; }
table.items td.num { text-align: right; white-space: nowrap; }
table.items small { color: #6b7280; }
table.items tfoot td { border-bottom: none; border-top: 2px solid #2f6f3e; font-weight: bold; padding-top: 8px; }
.footer-text { margin-top: 0.8cm; font-size: 9.5pt; color: #374151; white-space: pre-line; }
.preview-banner {
    background: #fef3c7; color: #92400e; padding: 6px 10px; border-radius: 4px;
    font-size: 9pt; margin-bottom: 0.4cm; text-align: center;
}
"""


@dataclass
class InvoicePdfLineItem:
    order_number: int
    name: str
    description: Optional[str]
    quantity: Decimal
    unit_price: Decimal
    line_total: Decimal


@dataclass
class InvoicePdfData:
    invoice_number: str  # placeholder like "Preview" if not yet finalized
    issued_date: date
    due_date: date
    subject: str
    recipient_names: str
    recipient_address: str
    parcel_plot_number: str
    parcel_area_sqm: Optional[float]
    line_items: List[InvoicePdfLineItem]
    subtotal: Decimal
    footer_text: Optional[str]
    is_preview: bool = False


def _substitute_placeholders(text: Optional[str], data: InvoicePdfData) -> str:
    if not text:
        return ""
    try:
        return text.format(
            invoice_number=data.invoice_number,
            parcel_number=data.parcel_plot_number,
            invoice_address=f"{data.recipient_names}\n{data.recipient_address}",
            due_date=data.due_date.strftime("%d.%m.%Y"),
        )
    except (KeyError, IndexError):
        return text


def render_invoice_pdf(
    data: InvoicePdfData, club_name: str, logo_path: Optional[Path],
    club_address_lines: List[str], bank_name: str, bank_iban: str, bank_bic: str,
    region: str, currency: str,
) -> bytes:
    logo_data_uri = file_to_data_uri(logo_path)
    logo_block = f'<img src="{logo_data_uri}">' if logo_data_uri else ""

    bank_bits = [b for b in [bank_name, f"IBAN {bank_iban}" if bank_iban else "", f"BIC {bank_bic}" if bank_bic else ""] if b]
    footer_line = " · ".join([*club_address_lines, *bank_bits])

    rows_html = []
    for li in data.line_items:
        desc_html = f"<br><small>{li.description}</small>" if li.description else ""
        # .normalize() strips trailing zeros (e.g. a DB-round-tripped
        # Numeric(10,2) 1.00 -> 1) so preview and finalized PDFs render
        # quantities identically regardless of Postgres's fixed scale.
        quantity_display = li.quantity.normalize()
        rows_html.append(f"""
        <tr>
            <td>{li.order_number}</td>
            <td>{li.name}{desc_html}</td>
            <td class="num">{quantity_display}</td>
            <td class="num">{format_money(li.unit_price, region, currency)}</td>
            <td class="num">{format_money(li.line_total, region, currency)}</td>
        </tr>
        """)

    preview_banner = '<div class="preview-banner">Preview — not yet finalized or sent</div>' if data.is_preview else ""
    footer_text_html = _substitute_placeholders(data.footer_text, data)

    html_doc = f"""
    <html>
    <head><meta charset="utf-8"><style>{PAGE_CSS}</style></head>
    <body>
        <div id="header">
            {logo_block}
            <div class="club-name">{club_name}</div>
        </div>
        <div id="footer">{footer_line}</div>

        {preview_banner}

        <div class="meta-block">
            <div class="recipient">{data.recipient_names}<br>{data.recipient_address.replace(chr(10), '<br>')}</div>
            <table class="invoice-meta">
                <tr><td>Invoice no.</td><td>{data.invoice_number}</td></tr>
                <tr><td>Date</td><td>{data.issued_date.strftime('%d.%m.%Y')}</td></tr>
                <tr><td>Due date</td><td>{data.due_date.strftime('%d.%m.%Y')}</td></tr>
            </table>
        </div>

        <h1>{data.subject}</h1>
        <div class="parcel-line">
            Parcel {data.parcel_plot_number}{f" · {data.parcel_area_sqm} m&sup2;" if data.parcel_area_sqm else ""}
        </div>

        <table class="items">
            <thead>
                <tr><th>#</th><th>Description</th><th>Qty</th><th>Unit price</th><th>Total</th></tr>
            </thead>
            <tbody>
                {''.join(rows_html)}
            </tbody>
            <tfoot>
                <tr><td colspan="4">Subtotal</td><td class="num">{format_money(data.subtotal, region, currency)}</td></tr>
            </tfoot>
        </table>

        {f'<div class="footer-text">{footer_text_html}</div>' if footer_text_html else ''}
    </body>
    </html>
    """
    return HTML(string=html_doc).write_pdf()
