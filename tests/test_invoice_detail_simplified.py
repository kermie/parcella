"""
Issues #175/#176/#177: the invoice detail page (/finances/invoices/{id})
was overloaded for what it's actually used for day-to-day -- reviewing
an overdue invoice and sending a reminder. Removed the Delivery status
card (#175), the Line items card (#176), and the Payments card (#177)
entirely -- confirmed with the reporter these aren't needed there
(delivery is trusted, line items are on the PDF, payments are recorded
from the run page instead, see issue #173). "Resend email" survives,
moved to the page's top bar instead of living inside the now-removed
Delivery card. The "Send reminder" button gets a short explanation of
what it actually does (#177's second ask).
"""
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import ClubSetting, Parcel, Member, MemberParcel, Invoice, MemberEmail


async def web_login(client, email: str = "admin@example.com", password: str = "testpasswort123") -> None:
    response = await client.post("/auth/login", data={"email": email, "password": password})
    assert response.status_code in (302, 303)


async def _enable_finances_module():
    async with AsyncSessionLocal() as session:
        session.add(ClubSetting(key="modul_finances", value="true", description="test"))
        await session.commit()


async def _make_invoice(client, plot_number="SIMPLIFY-1") -> str:
    async with AsyncSessionLocal() as session:
        member = Member(
            first_name="Simplify", last_name=plot_number,
            street="Gartenweg 1", postal_code="12345", city="Testort",
        )
        parcel = Parcel(plot_number=plot_number, area_sqm=100)
        session.add_all([member, parcel])
        await session.flush()
        session.add(MemberParcel(member_id=member.id, parcel_id=parcel.id, is_invoice_address=True))
        await session.commit()

    r_create = await client.post("/finances/runs", data={
        "year": "2026", "subject": "Simplify test", "issued_date": "2026-08-01",
        "due_date": "2026-09-01", "footer_text": "",
    })
    assert r_create.status_code in (302, 303)
    run_id = r_create.headers["location"].rstrip("/").split("/")[-1]

    r_item = await client.post(f"/finances/runs/{run_id}/items", data={
        "order_number": "10", "name": "Fee", "description": "",
        "pricing_mode": "fixed_per_parcel", "unit_price": "50.00",
        "applies_to_all_parcels": "on",
    })
    assert r_item.status_code in (302, 303)

    r_finalize = await client.post(f"/finances/runs/{run_id}/finalize")
    assert r_finalize.status_code in (302, 303), r_finalize.headers.get("location")

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Invoice).where(Invoice.invoice_run_id == run_id))
        invoice = result.scalar_one()
    return invoice.id


async def test_invoice_detail_no_longer_shows_delivery_line_items_or_payments_cards(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    invoice_id = await _make_invoice(client)

    response = await client.get(f"/finances/invoices/{invoice_id}")
    assert response.status_code == 200
    assert "UndefinedError" not in response.text

    assert "Delivery" not in response.text
    assert "Line items" not in response.text
    assert "Payments" not in response.text
    # The info and reminders cards must still be there.
    assert "Reminders" in response.text


async def test_resend_email_button_still_available_from_topbar(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    invoice_id = await _make_invoice(client)

    response = await client.get(f"/finances/invoices/{invoice_id}")
    assert response.status_code == 200
    assert f"/finances/invoices/{invoice_id}/resend-email" in response.text
    assert "Resend email" in response.text


async def test_resend_email_still_works(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    invoice_id = await _make_invoice(client)

    response = await client.post(f"/finances/invoices/{invoice_id}/resend-email")
    assert response.status_code in (302, 303)


async def test_send_reminder_button_has_explanatory_help_text(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    invoice_id = await _make_invoice(client)

    response = await client.get(f"/finances/invoices/{invoice_id}")
    assert response.status_code == 200
    assert "Generates the next reminder as a PDF" in response.text


async def test_sending_a_reminder_still_works_without_payments_card(client, admin_user):
    """The reminder-sending workflow itself must be entirely unaffected
    by removing the payments card from the same page."""
    await web_login(client)
    await _enable_finances_module()
    invoice_id = await _make_invoice(client)

    response = await client.post(f"/finances/invoices/{invoice_id}/reminders", data={
        "fee_amount": "5.00", "message": "Please pay soon.",
    })
    assert response.status_code in (302, 303)

    detail = await client.get(f"/finances/invoices/{invoice_id}")
    assert detail.status_code == 200
    assert "Reminder #1" in detail.text
