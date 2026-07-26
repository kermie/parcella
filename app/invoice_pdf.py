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
from app.i18n import translate

def _page_css(language: str) -> str:
    """@page CSS, with the "Page X of Y" page-numbering text localized.
    A function (not a constant) because that's the one static PDF
    string that can't be swapped via a running() element like the rest
    of the footer (issue #74's 4th column) -- WeasyPrint only supports
    page counters directly inside a margin box's own `content`
    property, not inside the DOM of an element placed there via
    `content: element(...)`, so it has to be baked into the CSS itself
    per render rather than translated in the HTML body."""
    page_word = translate("finances.pdf.page_label", language)
    of_word = translate("finances.pdf.of_label", language)
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
/* margin:0 -- the default UA body margin would otherwise throw off
   the address-window's exact DIN 5008 positioning below. */
body {{ margin: 0; font-family: 'DejaVu Sans', sans-serif; color: #1f2937; font-size: 10.5pt; }}
/* position:fixed on the page box itself (not the @top-center margin
   box, which is only as wide as the printable area) so the logo can
   sit at the true left edge of the A4 sheet and the club name can be
   centered on the page as a whole -- an absolutely-positioned
   full-width element for the name, independent of the logo's own
   width, so it's always exactly page-centered and never wraps. */
/* top/left are negative by exactly the @page margin (2.2cm/1.5cm) --
   WeasyPrint's containing block for `position: fixed` here is the
   page's own content box (inset by the @page margin), not the raw
   sheet, so this cancels that inset back out to the true page corner. */
#header {{ position: fixed; top: -2.2cm; left: -1.5cm; width: 21cm; height: 1.8cm; border-bottom: 2px solid #2f6f3e; }}
#header .header-logo {{ position: absolute; left: 0.5cm; top: 0.3cm; }}
#header .header-logo img {{ max-height: 50px; }}
#header .club-name {{
    position: absolute; left: 0; top: 0.65cm; width: 21cm; text-align: center;
    font-size: 13pt; font-weight: bold; color: #2f6f3e; white-space: nowrap;
}}
#footer {{
    position: running(footer); display: flex; gap: 0.6cm;
    font-size: 7.5pt; line-height: 1.4; color: #6b7280;
    border-top: 1px solid #d1d5db; padding-top: 6px;
}}
#footer .footer-col {{ flex: 1; min-width: 0; }}
#footer .footer-col:nth-child(1) {{ flex: 0.85; }}
#footer .footer-col:nth-child(3) {{ flex: 1.3; }}
.meta-block {{ display: flex; justify-content: space-between; margin-bottom: 0.8cm; }}
/* DIN 5008 Form A address window: page margin-top is 2.2cm, so the
   0.5cm padding-top here lands the sender line at 2.7cm from the page
   edge and the 1.8cm sender-line box ends exactly at 4.5cm, where the
   Anschriftzone (recipient address) must start; padding-left 0.5cm
   plus the 1.5cm page margin lands the whole window's left edge at
   the standard 2.0cm. */
.address-window {{ width: 8.5cm; padding-top: 0.5cm; padding-left: 0.5cm; box-sizing: content-box; }}
.sender-line {{
    height: 1.8cm; display: flex; align-items: flex-end;
    font-size: 7.5pt; color: #4b5563; border-bottom: 0.5pt solid #9ca3af;
    margin-bottom: 2pt;
}}
.recipient {{ white-space: pre-line; line-height: 1.5; }}
.invoice-meta td {{ padding: 1px 6px; }}
.invoice-meta td:first-child {{ color: #6b7280; }}
.invoice-meta td:last-child {{ font-weight: bold; text-align: right; }}
h1 {{ font-size: 14pt; margin-bottom: 0.1cm; color: #1f2937; }}
.parcel-line {{ color: #6b7280; margin-bottom: 0.5cm; font-size: 9.5pt; }}
table.items {{ width: 100%; border-collapse: collapse; margin-top: 0.3cm; }}
table.items th {{ text-align: left; font-size: 9pt; text-transform: uppercase; color: #4b5563; border-bottom: 2px solid #2f6f3e; padding: 6px 8px; }}
table.items td {{ padding: 7px 8px; border-bottom: 1px solid #e5e7eb; vertical-align: top; }}
table.items td.num {{ text-align: right; white-space: nowrap; }}
table.items small {{ color: #6b7280; }}
table.items tfoot td {{ border-bottom: none; border-top: 2px solid #2f6f3e; font-weight: bold; padding-top: 8px; }}
.footer-text {{ margin-top: 0.8cm; font-size: 9.5pt; color: #374151; white-space: pre-line; }}
.preview-banner {{
    background: #fef3c7; color: #92400e; padding: 6px 10px; border-radius: 4px;
    font-size: 9pt; margin-bottom: 0.4cm; text-align: center;
}}
.invoice-block + .invoice-block {{ page-break-before: always; }}
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
    parcel_plot_number: Optional[str]
    parcel_area_sqm: Optional[float]
    line_items: List[InvoicePdfLineItem]
    subtotal: Decimal
    footer_text: Optional[str]
    is_preview: bool = False


@dataclass
class ReminderPdfData:
    invoice_number: str
    level: int
    issued_date: date  # original invoice's issued date
    due_date: date  # original invoice's due date
    sent_date: date
    recipient_names: str
    recipient_address: str
    original_subtotal: Decimal
    paid_total: Decimal
    previous_fees_total: Decimal
    fee_amount: Decimal
    amount_due: Decimal
    message: Optional[str]


def invoice_pdf_data_from_invoice(invoice, run) -> InvoicePdfData:
    """Builds an InvoicePdfData from a real, persisted Invoice (with
    .line_items and .parcel eagerly loaded) and its InvoiceRun --
    shared by the single-invoice PDF route, the email-attachment path,
    and the print-bundle builder (see app/invoice_delivery.py), so
    there's exactly one place that knows how to turn an Invoice row
    into rendered PDF data. A member invoice (no parcel) simply omits
    the parcel line -- see _invoice_body_html."""
    return InvoicePdfData(
        invoice_number=invoice.invoice_number,
        issued_date=run.issued_date, due_date=run.due_date, subject=run.subject,
        recipient_names=invoice.recipient_names, recipient_address=invoice.recipient_address,
        parcel_plot_number=invoice.parcel.plot_number if invoice.parcel else None,
        parcel_area_sqm=invoice.parcel.area_sqm if invoice.parcel else None,
        line_items=[
            InvoicePdfLineItem(
                order_number=li.order_number, name=li.name, description=li.description,
                quantity=li.quantity, unit_price=li.unit_price, line_total=li.line_total,
            ) for li in sorted(invoice.line_items, key=lambda li: li.order_number)
        ],
        subtotal=invoice.subtotal, footer_text=run.footer_text, is_preview=False,
    )


def reminder_pdf_data_from_reminder(reminder, invoice, run) -> ReminderPdfData:
    """Builds a ReminderPdfData from a real, persisted InvoiceReminder
    (with `invoice.reminders`/`invoice.payments` eagerly loaded) and
    its Invoice/InvoiceRun. All amounts are computed from *current*
    live state (payments/other reminders' fees), not snapshotted at
    send time -- re-downloading an old reminder shows today's true
    balance rather than a frozen historical one, which is fine for a
    dunning notice (unlike Invoice's own recipient/address snapshot,
    there's no legal requirement here for the numbers to stay fixed)."""
    previous_fees_total = sum(
        (Decimal(str(r.fee_amount)) for r in invoice.reminders if r.level < reminder.level and r.fee_amount),
        Decimal("0"),
    )
    fee_amount = Decimal(str(reminder.fee_amount)) if reminder.fee_amount else Decimal("0")
    paid_total = Decimal(str(invoice.paid_total))
    original_subtotal = Decimal(str(invoice.subtotal))
    amount_due = max(Decimal("0"), original_subtotal + previous_fees_total + fee_amount - paid_total)
    return ReminderPdfData(
        invoice_number=invoice.invoice_number, level=reminder.level,
        issued_date=run.issued_date, due_date=run.due_date, sent_date=reminder.sent_at.date(),
        recipient_names=invoice.recipient_names, recipient_address=invoice.recipient_address,
        original_subtotal=original_subtotal, paid_total=paid_total,
        previous_fees_total=previous_fees_total, fee_amount=fee_amount, amount_due=amount_due,
        message=reminder.message,
    )


def _invoice_subject_slug(invoice) -> str:
    """The parcel's plot number, or -- for a member invoice with no
    parcel -- the member's last name, used as the filename segment
    identifying who/what an invoice PDF is for."""
    return invoice.parcel.plot_number if invoice.parcel else invoice.member.last_name


def invoice_pdf_filename(invoice, run) -> str:
    """Filename for `invoice`'s PDF -- {issued date YYYYMMDD}_{parcel
    plot number, or the member's last name for a member invoice}
    _invoice-{invoice number}.pdf, e.g. "20260724_G093_invoice-2026-500.pdf".
    Shared by the download route, email attachment, and cloud-storage
    upload (see app/invoice_delivery.py) so the naming stays consistent
    everywhere a PDF gets a filename. The invoice number's own "/" is
    swapped for "-" since it isn't valid in a filename -- the
    (club-configurable) invoice number format is otherwise left
    untouched."""
    date_part = run.issued_date.strftime("%Y%m%d")
    number_part = invoice.invoice_number.replace("/", "-")
    return f"{date_part}_{_invoice_subject_slug(invoice)}_invoice-{number_part}.pdf"


def reminder_pdf_filename(reminder, invoice, run) -> str:
    """Filename for a reminder's PDF -- {sent date YYYYMMDD}_{parcel
    plot number, or the member's last name}_reminder{level}-{invoice
    number}.pdf, mirroring invoice_pdf_filename's convention."""
    date_part = reminder.sent_at.strftime("%Y%m%d")
    number_part = invoice.invoice_number.replace("/", "-")
    return f"{date_part}_{_invoice_subject_slug(invoice)}_reminder{reminder.level}-{number_part}.pdf"


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


def _sender_line(club_name: str, club_address_lines: List[str]) -> str:
    """The DIN 5008 Form A "Einzeilige Rücksendeangabe" -- the club's
    own name/address compressed onto one small line, printed directly
    above the recipient's address so it stays visible through a
    windowed envelope (and tells the recipient/post office who to
    return the letter to)."""
    return " · ".join(filter(None, [club_name, *club_address_lines]))


def _invoice_body_html(data: InvoicePdfData, region: str, currency: str, language: str, sender_line: str) -> str:
    """The part of an invoice that's specific to it (recipient, meta,
    line items, footer text) -- everything except the page chrome
    (header/footer/@page CSS), which is shared across a whole document
    whether that document holds one invoice or a print bundle of many
    (see render_invoice_pdf / render_invoice_bundle_pdf)."""
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

    preview_banner = (
        f'<div class="preview-banner">{translate("finances.pdf.preview_banner", language)}</div>'
        if data.is_preview else ""
    )
    footer_text_html = _substitute_placeholders(data.footer_text, data)

    return f"""
    <div class="invoice-block">
        {preview_banner}

        <div class="meta-block">
            <div class="address-window">
                <div class="sender-line">{sender_line}</div>
                <div class="recipient">{data.recipient_names}<br>{data.recipient_address.replace(chr(10), '<br>')}</div>
            </div>
            <table class="invoice-meta">
                <tr><td>{translate("finances.pdf.invoice_no", language)}</td><td>{data.invoice_number}</td></tr>
                <tr><td>{translate("finances.pdf.date", language)}</td><td>{data.issued_date.strftime('%d.%m.%Y')}</td></tr>
                <tr><td>{translate("finances.pdf.due_date", language)}</td><td>{data.due_date.strftime('%d.%m.%Y')}</td></tr>
            </table>
        </div>

        <h1>{data.subject}</h1>
        {f'''<div class="parcel-line">
            {translate("finances.pdf.parcel", language)} {data.parcel_plot_number}{f" · {data.parcel_area_sqm} m&sup2;" if data.parcel_area_sqm else ""}
        </div>''' if data.parcel_plot_number else ''}

        <table class="items">
            <thead>
                <tr>
                    <th>#</th>
                    <th>{translate("finances.pdf.col_description", language)}</th>
                    <th>{translate("finances.pdf.col_qty", language)}</th>
                    <th>{translate("finances.pdf.col_unit_price", language)}</th>
                    <th>{translate("finances.pdf.col_total", language)}</th>
                </tr>
            </thead>
            <tbody>
                {''.join(rows_html)}
            </tbody>
            <tfoot>
                <tr><td colspan="4">{translate("finances.pdf.subtotal", language)}</td><td class="num">{format_money(data.subtotal, region, currency)}</td></tr>
            </tfoot>
        </table>

        {f'<div class="footer-text">{footer_text_html}</div>' if footer_text_html else ''}
    </div>
    """


def _reminder_body_html(data: ReminderPdfData, region: str, currency: str, language: str, sender_line: str) -> str:
    """The part of a reminder that's specific to it -- same page chrome
    as an invoice (see render_reminder_pdf), but a much simpler body:
    a reference back to the original invoice, an itemized amount-due
    breakdown (only showing rows that actually apply), and an optional
    free-text message (issue #59's "let me decide whether to add a
    fee" -- fee/previous-fee rows just don't render when there's
    nothing to show)."""
    rows = [(translate("finances.reminder_pdf.row_original_amount", language), data.original_subtotal)]
    if data.paid_total:
        rows.append((translate("finances.reminder_pdf.row_paid", language), -data.paid_total))
    if data.previous_fees_total:
        rows.append((translate("finances.reminder_pdf.row_previous_fees", language), data.previous_fees_total))
    if data.fee_amount:
        rows.append((translate("finances.reminder_pdf.row_this_fee", language), data.fee_amount))

    rows_html = "".join(
        f'<tr><td>{label}</td><td class="num">{format_money(amount, region, currency)}</td></tr>'
        for label, amount in rows
    )
    message_html = f'<div class="footer-text">{data.message}</div>' if data.message else ""

    return f"""
    <div class="invoice-block">
        <div class="meta-block">
            <div class="address-window">
                <div class="sender-line">{sender_line}</div>
                <div class="recipient">{data.recipient_names}<br>{data.recipient_address.replace(chr(10), '<br>')}</div>
            </div>
            <table class="invoice-meta">
                <tr><td>{translate("finances.pdf.invoice_no", language)}</td><td>{data.invoice_number}</td></tr>
                <tr><td>{translate("finances.pdf.date", language)}</td><td>{data.sent_date.strftime('%d.%m.%Y')}</td></tr>
                <tr><td>{translate("finances.pdf.due_date", language)}</td><td>{data.due_date.strftime('%d.%m.%Y')}</td></tr>
            </table>
        </div>

        <h1>{translate("finances.reminder_pdf.heading", language, level=data.level)}</h1>
        <div class="parcel-line">
            {translate("finances.reminder_pdf.reference_line", language, invoice_number=data.invoice_number, date=data.issued_date.strftime('%d.%m.%Y'))}
        </div>

        <p>{translate("finances.reminder_pdf.intro", language)}</p>

        <table class="items">
            <tbody>
                {rows_html}
            </tbody>
            <tfoot>
                <tr><td>{translate("finances.pdf.subtotal", language)}</td><td class="num">{format_money(data.amount_due, region, currency)}</td></tr>
            </tfoot>
        </table>

        <p class="footer-text">{translate("finances.reminder_pdf.closing", language)}</p>
        {message_html}
    </div>
    """


def _wrap_document(body_html: str, club_name: str, logo_path: Optional[Path], footer_html: str, language: str) -> str:
    logo_data_uri = file_to_data_uri(logo_path)
    logo_block = f'<img src="{logo_data_uri}">' if logo_data_uri else ""
    return f"""
    <html>
    <head><meta charset="utf-8"><style>{_page_css(language)}</style></head>
    <body>
        <div id="header">
            <div class="header-logo">{logo_block}</div>
            <div class="club-name">{club_name}</div>
        </div>
        <div id="footer">{footer_html}</div>
        {body_html}
    </body>
    </html>
    """


def _footer_html(
    club_name: str, club_address_lines: List[str], register_court: str, register_number: str,
    bank_name: str, bank_iban: str, bank_bic: str, bank_account_owner: str, language: str,
) -> str:
    """Three-column footer content (issue #74): organization identity,
    register-court info, and bank details, laid out side by side via
    the flex #footer running element. "Page X of Y" is the visual
    fourth column, but lives in its own @bottom-right margin box (see
    _page_css) rather than here, since WeasyPrint only resolves page
    counters directly inside a margin box's own `content`, not inside
    the DOM of an element placed there via `content: element(...)`."""
    org_lines = [club_name, *club_address_lines]

    register_line = " ".join(filter(None, [register_court, register_number]))
    register_lines = [register_line] if register_line else []

    bank_line = " · ".join(filter(None, [bank_name, f"BIC {bank_bic}" if bank_bic else ""]))
    bank_lines = [b for b in [
        bank_line,
        f"IBAN {bank_iban}" if bank_iban else "",
        translate("finances.pdf.account_holder", language, name=bank_account_owner) if bank_account_owner else "",
    ] if b]

    def column(lines: List[str]) -> str:
        return f'<div class="footer-col">{"".join(f"<div>{line}</div>" for line in lines)}</div>'

    return column(org_lines) + column(register_lines) + column(bank_lines)


def render_invoice_pdf(
    data: InvoicePdfData, club_name: str, logo_path: Optional[Path],
    club_address_lines: List[str], bank_name: str, bank_iban: str, bank_bic: str,
    region: str, currency: str, bank_account_owner: str = "", language: str = "en",
    register_court: str = "", register_number: str = "",
) -> bytes:
    footer_html = _footer_html(
        club_name, club_address_lines, register_court, register_number,
        bank_name, bank_iban, bank_bic, bank_account_owner, language,
    )
    sender_line = _sender_line(club_name, club_address_lines)
    html_doc = _wrap_document(
        _invoice_body_html(data, region, currency, language, sender_line), club_name, logo_path, footer_html, language,
    )
    return HTML(string=html_doc).write_pdf()


def render_invoice_bundle_pdf(
    items: List[InvoicePdfData], club_name: str, logo_path: Optional[Path],
    club_address_lines: List[str], bank_name: str, bank_iban: str, bank_bic: str,
    region: str, currency: str, bank_account_owner: str = "", language: str = "en",
    register_court: str = "", register_number: str = "",
) -> bytes:
    """Same rendering as render_invoice_pdf, but for many invoices in
    one PDF (issue #58's "merge PDFs to one big one so we can print
    it") -- a page-break-before between each invoice's block, sharing
    one @page header/footer/page-numbering across the whole bundle
    rather than resetting per invoice, since it's meant to be printed
    and handled as a single stack."""
    footer_html = _footer_html(
        club_name, club_address_lines, register_court, register_number,
        bank_name, bank_iban, bank_bic, bank_account_owner, language,
    )
    sender_line = _sender_line(club_name, club_address_lines)
    body_html = "".join(_invoice_body_html(data, region, currency, language, sender_line) for data in items)
    html_doc = _wrap_document(body_html, club_name, logo_path, footer_html, language)
    return HTML(string=html_doc).write_pdf()


def render_reminder_pdf(
    data: ReminderPdfData, club_name: str, logo_path: Optional[Path],
    club_address_lines: List[str], bank_name: str, bank_iban: str, bank_bic: str,
    region: str, currency: str, bank_account_owner: str = "", language: str = "en",
    register_court: str = "", register_number: str = "",
) -> bytes:
    """Same page chrome as render_invoice_pdf (issue #59) -- header,
    four-column footer, page numbering -- with a reminder-specific body."""
    footer_html = _footer_html(
        club_name, club_address_lines, register_court, register_number,
        bank_name, bank_iban, bank_bic, bank_account_owner, language,
    )
    sender_line = _sender_line(club_name, club_address_lines)
    html_doc = _wrap_document(
        _reminder_body_html(data, region, currency, language, sender_line), club_name, logo_path, footer_html, language,
    )
    return HTML(string=html_doc).write_pdf()
