"""
Email channel for the announcements module.

Recipients are current parcel residents (MemberParcel.assigned_until IS
NULL -- same "present tenant" definition used elsewhere in the app,
e.g. insurance household grouping) who are not soft-deleted, whose
membership hasn't ended, and who have email_notifications = True (this
is the "e-mail info = yes" flag from the original feature request).
A member with no stored email address is silently skipped rather than
treated as an error -- that's a data-completeness issue for Members
admin, not an announcements-sending failure.

Uses the exact same body_html as the blog channel (see
docs/module-announcements.md -- "same container" is a deliberate
product decision, not an oversight), wrapped in a minimal email
template with the header image and the club's branding.

Sending is per-recipient, but AnnouncementDelivery tracks channel-level
status only (not one row per recipient) -- see the model's docstring
in app/models.py for why. A partial failure (some recipients bounced/
failed) still counts as SENT, with the failure count noted in
error_message, since the announcement did go out; a total failure
(zero successful sends, or zero recipients found) is recorded as
FAILED so it's visually distinct and inviting a retry.

Sending is paced (EMAIL_BATCH_SIZE per EMAIL_BATCH_PAUSE_SECONDS)
rather than firing every email at once, to avoid tripping an SMTP
relay's rate limits on a large club roster. Because pacing an 800-name
list at a handful per minute can take well over an hour, the actual
send runs as a FastAPI background task (see run_paced_email_send,
scheduled from the router) rather than inside the request/response
cycle -- the endpoint marks the delivery SENDING and returns
immediately, and the background task updates the same
AnnouncementDelivery row as it progresses and again when it finishes.
The background task opens its own DB session (AsyncSessionLocal),
since the request-scoped session closes once the response has been
sent -- same pattern as app.main's ticket-inbox polling loop.
"""
import asyncio
import logging
from datetime import date, datetime, timezone
from typing import List, Optional, Tuple

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import AsyncSessionLocal
from app.models import (
    Announcement, AnnouncementChannel, AnnouncementDelivery,
    AnnouncementDeliveryStatus, Member, MemberParcel,
)
from app.email_service import sende_email
from app.branding import load_branding

logger = logging.getLogger(__name__)

# Deliberately conservative default -- "five or ten per minute" was the
# ask. Both numbers are read at call time (not baked into a constant
# closure), so a future admin-configurable setting can replace these
# without changing the pacing logic itself.
EMAIL_BATCH_SIZE = 8
EMAIL_BATCH_PAUSE_SECONDS = 60


async def get_active_recipient_emails(db: AsyncSession) -> List[Tuple[Member, str]]:
    """Current parcel residents with email_notifications=True and at
    least one stored email address. One (member, email) pair per
    member, using their primary email if marked, otherwise the first
    one on file."""
    today = date.today()
    result = await db.execute(
        select(Member)
        .join(MemberParcel, MemberParcel.member_id == Member.id)
        .where(
            MemberParcel.assigned_until.is_(None),
            Member.deleted_at.is_(None),
            Member.email_notifications.is_(True),
            or_(Member.member_until.is_(None), Member.member_until >= today),
        )
        .options(selectinload(Member.email_addresses))
        .distinct()
    )
    members = result.scalars().unique().all()

    recipients: List[Tuple[Member, str]] = []
    for member in members:
        emails = member.email_addresses
        if not emails:
            continue
        primary = next((e for e in emails if e.is_primary), emails[0])
        recipients.append((member, primary.address))
    return recipients


async def _resolve_image_url(announcement: Announcement, base_url: str) -> Optional[str]:
    return f"{base_url}{announcement.image_url}" if announcement.image_url else None


def build_announcement_email_html(
    announcement: Announcement, club_name: str, image_url: Optional[str], test_banner: bool = False,
) -> str:
    """Wraps the announcement's canonical body_html (same content as
    the blog draft) in a minimal, branded email shell. test_banner adds
    a small notice at the top so a test send is never mistaken for the
    real thing, without changing the actual announcement content being
    previewed."""
    image_block = (
        f'<p><img src="{image_url}" alt="" style="max-width: 100%; height: auto; border-radius: 4px;"></p>'
        if image_url else ""
    )
    banner = (
        '<p style="background: #fef3c7; color: #92400e; padding: 8px 12px; border-radius: 4px; '
        'font-size: 0.85rem;">This is a test send and has not gone out to any members.</p>'
        if test_banner else ""
    )
    return f"""
    <html><body style="font-family: sans-serif; color: #1f2937; max-width: 600px; margin: 0 auto;">
    {banner}
    <p style="color: #6b7280; font-size: 0.85rem; margin-bottom: 0.5rem;">{club_name}</p>
    <h2 style="margin-top: 0;">{announcement.title}</h2>
    {image_block}
    <div>{announcement.body_html}</div>
    </body></html>
    """


async def send_test_email(announcement: Announcement, db: AsyncSession, request, address: str) -> bool:
    """Sends the announcement content to a single address for review,
    completely separate from the real send: it doesn't touch
    AnnouncementDelivery and isn't paced, since it's exactly one
    email."""
    branding = await load_branding(db)
    base_url = str(request.base_url).rstrip("/")
    image_url = await _resolve_image_url(announcement, base_url)
    html_body = build_announcement_email_html(announcement, branding["club_name"], image_url, test_banner=True)
    return await sende_email(address, f"[Test] {announcement.title}", html_body, db=db)


async def start_paced_email_send(announcement: Announcement, db: AsyncSession) -> Tuple[AnnouncementDelivery, int]:
    """Synchronous part of sending, run inside the request: figures out
    who would receive it and marks the delivery row SENDING (or FAILED
    immediately if there's no one to send to). Returns the delivery row
    and recipient count so the router can decide whether to schedule
    the background task at all. Caller commits."""
    recipients = await get_active_recipient_emails(db)

    delivery = announcement.delivery_for(AnnouncementChannel.EMAIL)
    if delivery is None:
        delivery = AnnouncementDelivery(announcement_id=announcement.id, channel=AnnouncementChannel.EMAIL)
        db.add(delivery)

    if not recipients:
        delivery.status = AnnouncementDeliveryStatus.FAILED
        delivery.error_message = "No recipients found (no current residents with email notifications enabled and a stored email address)."
        delivery.sent_at = datetime.now(timezone.utc)
        return delivery, 0

    delivery.status = AnnouncementDeliveryStatus.SENDING
    delivery.error_message = f"0 of {len(recipients)} sent so far."
    delivery.sent_at = None
    return delivery, len(recipients)


async def run_paced_email_send(announcement_id: str, base_url: str) -> None:
    """Background task: does the actual paced sending and updates the
    EMAIL AnnouncementDelivery row as it goes, then a final time on
    completion. Runs after the triggering request has already
    returned, so it opens its own DB session rather than reusing the
    request's (which is closed by then) -- same pattern as
    app.main's _ticket_inbox_polling_loop.
    """
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Announcement).where(Announcement.id == announcement_id))
            announcement = result.scalar_one_or_none()
            if announcement is None:
                return

            branding = await load_branding(db)
            image_url = await _resolve_image_url(announcement, base_url)
            html_body = build_announcement_email_html(announcement, branding["club_name"], image_url)

            recipients = await get_active_recipient_emails(db)

            delivery_result = await db.execute(
                select(AnnouncementDelivery).where(
                    AnnouncementDelivery.announcement_id == announcement_id,
                    AnnouncementDelivery.channel == AnnouncementChannel.EMAIL,
                )
            )
            delivery = delivery_result.scalar_one_or_none()
            if delivery is None:
                return

            success_count = 0
            failure_count = 0
            for batch_start in range(0, len(recipients), EMAIL_BATCH_SIZE):
                batch = recipients[batch_start:batch_start + EMAIL_BATCH_SIZE]
                for _member, address in batch:
                    sent = await sende_email(address, announcement.title, html_body, db=db)
                    if sent:
                        success_count += 1
                    else:
                        failure_count += 1

                delivery.error_message = f"{success_count + failure_count} of {len(recipients)} sent so far."
                await db.commit()

                is_last_batch = batch_start + EMAIL_BATCH_SIZE >= len(recipients)
                if not is_last_batch:
                    await asyncio.sleep(EMAIL_BATCH_PAUSE_SECONDS)

            delivery.sent_at = datetime.now(timezone.utc)
            if success_count == 0:
                delivery.status = AnnouncementDeliveryStatus.FAILED
                delivery.error_message = f"All {failure_count} send attempts failed (check SMTP configuration under Admin -> Settings)."
            else:
                delivery.status = AnnouncementDeliveryStatus.SENT
                delivery.error_message = (
                    f"{failure_count} of {len(recipients)} recipients could not be reached." if failure_count else None
                )
            await db.commit()
    except Exception:
        logger.exception(f"Announcement email send failed for announcement {announcement_id}")
        try:
            async with AsyncSessionLocal() as db:
                delivery_result = await db.execute(
                    select(AnnouncementDelivery).where(
                        AnnouncementDelivery.announcement_id == announcement_id,
                        AnnouncementDelivery.channel == AnnouncementChannel.EMAIL,
                    )
                )
                delivery = delivery_result.scalar_one_or_none()
                if delivery is not None:
                    delivery.status = AnnouncementDeliveryStatus.FAILED
                    delivery.error_message = "Sending stopped unexpectedly; please check the server logs and try again."
                    delivery.sent_at = datetime.now(timezone.utc)
                    await db.commit()
        except Exception:
            logger.exception(f"Could not even record the failure for announcement {announcement_id}")
