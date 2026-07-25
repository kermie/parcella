"""
Issue #77: only invoice-address members who also opted into email
notifications should be emailed; the print bundle must contain only
those with is_invoice_address=True and email_notifications=False --
regardless of whether /deliver has run yet (see app/invoice_delivery.py
and the print-bundle filter in app/routers/finances.py).
"""
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import AsyncSessionLocal
from app.models import Member, MemberEmail, MemberParcel, Parcel, Invoice, ClubSetting


async def _enable_finances_module():
    """Finances defaults to off (see app/module_flags.py); enable it
    directly via ClubSetting, same pattern test_smoke_templates.py uses."""
    async with AsyncSessionLocal() as session:
        session.add(ClubSetting(key="modul_finances", value="true", description="test"))
        await session.commit()


async def _make_run(client, year="2026"):
    r_create = await client.post("/finances/runs", data={
        "year": year, "subject": "Delivery test", "issued_date": f"{year}-08-01",
        "due_date": f"{year}-09-01", "footer_text": "",
    })
    assert r_create.status_code in (302, 303)
    return r_create.headers["location"].rstrip("/").split("/")[-1]


async def test_print_bundle_excludes_email_eligible_members_even_before_deliver(client, admin_user, monkeypatch):
    """The core bug in #77: calling print-bundle before deliver used to
    include every invoice (emailed_at was still None for everyone), so
    an email-opted-in member's invoice would end up printed AND later
    emailed. The filter must be based on recipient eligibility, not on
    the emailed_at side effect."""
    async def fake_send_email(recipient, subject, html_body, text_body=None, db=None, attachments=None):
        return True

    monkeypatch.setattr("app.invoice_delivery.send_email", fake_send_email)

    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    await _enable_finances_module()

    async with AsyncSessionLocal() as session:
        email_member = Member(
            first_name="Email", last_name="Reachable",
            street="Test-Str 1", postal_code="12345", city="Testort",
            email_notifications=True,
        )
        print_member = Member(
            first_name="Print", last_name="Only",
            street="Test-Str 2", postal_code="12345", city="Testort",
            email_notifications=False,
        )
        email_parcel = Parcel(plot_number="ISSUE77-EMAIL", area_sqm=100)
        print_parcel = Parcel(plot_number="ISSUE77-PRINT", area_sqm=100)
        session.add_all([email_member, print_member, email_parcel, print_parcel])
        await session.flush()
        session.add(MemberEmail(member_id=email_member.id, address="reachable@example.com", is_primary=True))
        session.add(MemberParcel(member_id=email_member.id, parcel_id=email_parcel.id, is_invoice_address=True))
        session.add(MemberParcel(member_id=print_member.id, parcel_id=print_parcel.id, is_invoice_address=True))
        await session.commit()

    run_id = await _make_run(client)
    r_item = await client.post(f"/finances/runs/{run_id}/items", data={
        "order_number": "10", "name": "Membership fee", "description": "",
        "pricing_mode": "fixed_per_parcel", "unit_price": "12.50", "applies_to_all_parcels": "on",
    })
    assert r_item.status_code in (302, 303)

    r_finalize = await client.post(f"/finances/runs/{run_id}/finalize")
    assert r_finalize.status_code in (302, 303), r_finalize.headers.get("location")

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Invoice)
            .options(selectinload(Invoice.parcel))
            .where(Invoice.invoice_run_id == run_id)
        )
        invoices_by_parcel = {i.parcel.plot_number: i for i in result.scalars().unique().all()}
    email_invoice_id = invoices_by_parcel["ISSUE77-EMAIL"].id
    print_invoice_id = invoices_by_parcel["ISSUE77-PRINT"].id

    # Print bundle requested BEFORE /deliver ever ran.
    r_bundle = await client.get(f"/finances/runs/{run_id}/print-bundle")
    assert r_bundle.status_code == 200
    assert r_bundle.headers["content-type"] == "application/pdf"

    async with AsyncSessionLocal() as session:
        email_invoice = await session.get(Invoice, email_invoice_id)
        print_invoice = await session.get(Invoice, print_invoice_id)
        assert email_invoice.printed_at is None, "email-eligible member must never be printed"
        assert print_invoice.printed_at is not None, "print-only member must be in the bundle"
        assert email_invoice.emailed_at is None, "deliver hasn't run yet"

    # Now deliver: the email-eligible member gets emailed; the
    # print-only member is untouched (no stored email at all).
    r_deliver = await client.post(f"/finances/runs/{run_id}/deliver")
    assert r_deliver.status_code in (302, 303)
    assert "emailed=1" in r_deliver.headers["location"]

    async with AsyncSessionLocal() as session:
        email_invoice = await session.get(Invoice, email_invoice_id)
        print_invoice = await session.get(Invoice, print_invoice_id)
        assert email_invoice.emailed_at is not None
        assert email_invoice.printed_at is None, "must still never be printed, even after deliver"
        assert print_invoice.emailed_at is None


async def test_print_bundle_excludes_invoice_address_member_with_email_notifications_off_but_no_email(client, admin_user):
    """A member with is_invoice_address=True and email_notifications=False
    but with a stored email address is still print-only -- opting out of
    email notifications takes precedence, matching issue #77's explicit
    address=true AND email_info=false rule for the print bundle."""
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    await _enable_finances_module()

    async with AsyncSessionLocal() as session:
        member = Member(
            first_name="OptedOut", last_name="ButHasEmail",
            street="Test-Str 3", postal_code="12345", city="Testort",
            email_notifications=False,
        )
        parcel = Parcel(plot_number="ISSUE77-OPTOUT", area_sqm=100)
        session.add_all([member, parcel])
        await session.flush()
        session.add(MemberEmail(member_id=member.id, address="optedout@example.com", is_primary=True))
        session.add(MemberParcel(member_id=member.id, parcel_id=parcel.id, is_invoice_address=True))
        await session.commit()

    run_id = await _make_run(client, year="2027")
    await client.post(f"/finances/runs/{run_id}/items", data={
        "order_number": "10", "name": "Membership fee", "description": "",
        "pricing_mode": "fixed_per_parcel", "unit_price": "12.50", "applies_to_all_parcels": "on",
    })
    r_finalize = await client.post(f"/finances/runs/{run_id}/finalize")
    assert r_finalize.status_code in (302, 303)

    r_deliver = await client.post(f"/finances/runs/{run_id}/deliver")
    assert "emailed=0" in r_deliver.headers["location"]

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Invoice).where(Invoice.invoice_run_id == run_id))
        invoice = result.scalars().first()
    assert invoice.emailed_at is None

    r_bundle = await client.get(f"/finances/runs/{run_id}/print-bundle")
    assert r_bundle.status_code == 200

    async with AsyncSessionLocal() as session:
        invoice = await session.get(Invoice, invoice.id)
        assert invoice.printed_at is not None
