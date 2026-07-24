"""
Delivery for finalized invoices (issue #58): email with the invoice
PDF attached, upload to the parcel's cloud-storage folder, and a
merged print bundle for members who aren't reachable by email.

Recipient resolution is re-derived at send time (not from the
Invoice's recipient_names/-address snapshot) since delivery can happen
well after a run is finalized, and membership may have changed since.
"""
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Invoice, InvoiceRun, MemberParcel, Member
from app.email_service import send_email
from app.parcel_cloud_folders import get_active_folder
from app.cloud_storage import CloudStorageError, NextcloudProvider
from app.i18n import t_for
from app.invoice_pdf import (
    InvoicePdfData, render_invoice_pdf, render_invoice_bundle_pdf, invoice_pdf_data_from_invoice,
)


async def _invoice_recipient(db: AsyncSession, invoice: Invoice) -> Optional[Tuple[Member, str]]:
    """The (member, email) to send `invoice` to, if any current
    invoice-address resident of its parcel has email_notifications=True
    and a stored email address."""
    result = await db.execute(
        select(MemberParcel)
        .options(selectinload(MemberParcel.member).selectinload(Member.email_addresses))
        .where(
            MemberParcel.parcel_id == invoice.parcel_id,
            MemberParcel.assigned_until.is_(None),
            MemberParcel.is_invoice_address.is_(True),
        )
    )
    for assignment in result.scalars().all():
        member = assignment.member
        if not member.email_notifications or not member.email_addresses:
            continue
        primary = next((e for e in member.email_addresses if e.is_primary), member.email_addresses[0])
        return member, primary.address
    return None


async def send_invoice_email(
    request: Request, db: AsyncSession, invoice: Invoice, run: InvoiceRun, pdf_context: dict,
) -> bool:
    """Emails `invoice`'s PDF to its parcel's invoice-address member,
    if reachable. Returns whether an email was actually sent; sets
    invoice.emailed_at on success. Caller commits."""
    recipient = await _invoice_recipient(db, invoice)
    if recipient is None:
        return False
    member, email_address = recipient

    data = invoice_pdf_data_from_invoice(invoice, run)
    pdf_bytes = render_invoice_pdf(data, **pdf_context)

    subject = t_for(request, "email.invoice_delivery.subject", invoice_number=invoice.invoice_number, club_name=pdf_context["club_name"])
    html = f"""
    <html><body style="font-family: sans-serif;">
    <p>{t_for(request, "email.invoice_delivery.greeting", name=member.full_name)}</p>
    <p>{t_for(request, "email.invoice_delivery.body", club_name=pdf_context["club_name"], parcel_number=invoice.parcel.plot_number, due_date=run.due_date.strftime("%d.%m.%Y"))}</p>
    </body></html>
    """
    filename = f"invoice_{invoice.invoice_number.replace('/', '-')}.pdf"
    sent = await send_email(
        email_address, subject, html, db=db,
        attachments=[(filename, pdf_bytes, "application/pdf")],
    )
    if sent:
        invoice.emailed_at = datetime.now(timezone.utc)
    return sent


async def upload_invoice_to_cloud(
    db: AsyncSession, invoice: Invoice, run: InvoiceRun, pdf_context: dict, provider: Optional[NextcloudProvider],
) -> bool:
    """Uploads `invoice`'s PDF to its parcel's active cloud-storage
    folder, if one is configured. `provider` is resolved once by the
    caller (app.cloud_storage.get_nextcloud_provider) and reused across
    every invoice in a run, rather than reconnecting per invoice.
    Returns whether it was actually uploaded (silently skipped -- not
    an error -- if cloud storage isn't configured or the parcel has no
    folder assigned, same as the rest of the app treats this as
    opt-in). Sets invoice.uploaded_to_cloud_at on success. Caller
    commits."""
    if provider is None:
        return False
    folder = await get_active_folder(db, invoice.parcel_id)
    if folder is None:
        return False

    data = invoice_pdf_data_from_invoice(invoice, run)
    pdf_bytes = render_invoice_pdf(data, **pdf_context)
    filename = f"invoice_{invoice.invoice_number.replace('/', '-')}.pdf"

    try:
        await provider.upload_file(folder.relative_path, filename, pdf_bytes)
    except CloudStorageError:
        return False

    invoice.uploaded_to_cloud_at = datetime.now(timezone.utc)
    return True


async def build_print_bundle(db: AsyncSession, invoices: List[Invoice], run: InvoiceRun, pdf_context: dict) -> bytes:
    """Merges `invoices` into one print-ready PDF (issue #58's "merge
    PDFs to one big one so we can print it") and marks each as printed.
    Caller decides which invoices to include (see the router: members
    without email notifications) and commits."""
    items = [invoice_pdf_data_from_invoice(invoice, run) for invoice in invoices]
    now = datetime.now(timezone.utc)
    for invoice in invoices:
        invoice.printed_at = now
    return render_invoice_bundle_pdf(items, **pdf_context)
