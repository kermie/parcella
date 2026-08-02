"""
Issue #188: suggest possible open outgoing/incoming invoice matches
for an account booking, by amount first then counterparty name --
confirmed with the reporter that picking a suggestion creates a real
InvoicePayment/IncomingInvoicePayment, reopening ADR 0059's
reconciliation-is-out-of-scope stance specifically for this case.
"""
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.invoice_matching import find_matching_invoices, best_match_option
from app.models import ClubSetting, IncomingInvoice, IncomingInvoiceLineItem, Invoice, Member, MemberParcel, Parcel


async def web_login(client, email: str = "admin@example.com", password: str = "testpasswort123") -> None:
    response = await client.post("/auth/login", data={"email": email, "password": password})
    assert response.status_code in (302, 303)


async def _enable_finances_module():
    async with AsyncSessionLocal() as session:
        session.add(ClubSetting(key="modul_finances", value="true", description="test"))
        await session.commit()


async def _make_run_with_invoice(client, plot_number, year, unit_price):
    """Scopes the item to just this test's own new parcel (rather than
    applies_to_all_parcels) -- tests in this module create several
    runs for the same year in one test, and "all parcels" would also
    pick up parcels from an earlier call in the same test."""
    async with AsyncSessionLocal() as session:
        member = Member(
            first_name="Invoice", last_name=plot_number,
            street="Gartenweg 1", postal_code="12345", city="Testort",
        )
        parcel = Parcel(plot_number=plot_number, area_sqm=100)
        session.add_all([member, parcel])
        await session.flush()
        session.add(MemberParcel(member_id=member.id, parcel_id=parcel.id, is_invoice_address=True))
        await session.commit()
        parcel_id = parcel.id

    r_create = await client.post("/finances/runs", data={
        "year": str(year), "subject": "Matching test", "issued_date": f"{year}-01-01",
        "due_date": f"{year}-02-01", "footer_text": "",
    })
    assert r_create.status_code in (302, 303)
    run_id = r_create.headers["location"].rstrip("/").split("/")[-1]

    r_item = await client.post(f"/finances/runs/{run_id}/items", data={
        "order_number": "0", "name": "Fee", "description": "",
        "pricing_mode": "fixed_per_parcel", "unit_price": unit_price,
        "parcel_ids": [parcel_id],
    })
    assert r_item.status_code in (302, 303)

    r_finalize = await client.post(f"/finances/runs/{run_id}/finalize")
    assert r_finalize.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Invoice).where(Invoice.invoice_run_id == run_id))
        return result.scalar_one()


async def test_matches_open_outgoing_invoice_by_amount(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    invoice = await _make_run_with_invoice(client, "MATCH-A", 2050, "123.45")

    async with AsyncSessionLocal() as session:
        matches = await find_matching_invoices(session, Decimal("123.45"), "")

    assert any(m.kind == "invoice" and m.id == invoice.id for m in matches)


async def test_no_match_for_fully_paid_invoice(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    invoice = await _make_run_with_invoice(client, "MATCH-B", 2051, "80.00")

    await client.post(f"/finances/invoices/{invoice.id}/payments", data={
        "amount": "80.00", "paid_on": "2051-03-01", "note": "",
    })

    async with AsyncSessionLocal() as session:
        matches = await find_matching_invoices(session, Decimal("80.00"), "")

    assert not any(m.id == invoice.id for m in matches)


async def test_name_matches_flag_and_best_match_selection(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    invoice = await _make_run_with_invoice(client, "MATCH-C", 2052, "60.00")

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Invoice).where(Invoice.id == invoice.id))
        recipient_names = result.scalar_one().recipient_names

    async with AsyncSessionLocal() as session:
        matches = await find_matching_invoices(session, Decimal("60.00"), recipient_names)

    match = next(m for m in matches if m.id == invoice.id)
    assert match.name_matches is True
    assert best_match_option(matches) == match.option_value


async def test_negative_amount_matches_incoming_invoice(client, admin_user):
    await web_login(client)
    await _enable_finances_module()

    async with AsyncSessionLocal() as session:
        incoming = IncomingInvoice(sender="Matching Supplier", invoice_date=date.fromisoformat("2053-01-01"))
        session.add(incoming)
        await session.flush()
        session.add(IncomingInvoiceLineItem(incoming_invoice_id=incoming.id, amount="42.00"))
        await session.commit()
        incoming_id = incoming.id

    async with AsyncSessionLocal() as session:
        matches = await find_matching_invoices(session, Decimal("-42.00"), "Matching Supplier")

    assert any(m.kind == "incoming_invoice" and m.id == incoming_id and m.name_matches for m in matches)


async def test_best_match_is_none_when_multiple_name_matches(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    invoice_a = await _make_run_with_invoice(client, "MATCH-D", 2054, "99.00")
    invoice_b = await _make_run_with_invoice(client, "MATCH-E", 2054, "99.00")

    async with AsyncSessionLocal() as session:
        matches = await find_matching_invoices(session, Decimal("99.00"), "")

    ids = {m.id for m in matches}
    assert {invoice_a.id, invoice_b.id} <= ids
    assert best_match_option(matches) == "none"
