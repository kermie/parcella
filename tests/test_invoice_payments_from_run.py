"""
Issue #173: recording a payment against a finalized invoice used to
only be reachable from that invoice's own detail page
(/finances/invoices/{id}) -- an admin working through a whole run
(e.g. reconciling a bank statement) had to click into each invoice
separately. /finances/runs/{run_id} now shows each invoice's payment
status and lets you record a payment (including a partial one, tagged
to a FinanceAccount) directly from there, same fields/route as the
invoice detail page (POST /finances/invoices/{id}/payments), just with
an added from_run hint so the redirect lands back on the run page
instead of navigating away.
"""
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import AsyncSessionLocal
from app.models import ClubSetting, Parcel, Member, MemberParcel, Invoice, InvoicePayment, FinanceAccount, FinanceAccountType


async def _enable_finances_module():
    async with AsyncSessionLocal() as session:
        session.add(ClubSetting(key="modul_finances", value="true", description="test"))
        await session.commit()


async def _make_run_with_invoice(client, year=2026, plot_number="PAYFROMRUN"):
    async with AsyncSessionLocal() as session:
        member = Member(
            first_name="Pay", last_name="FromRun",
            street="Gartenweg 1", postal_code="12345", city="Testort",
        )
        parcel = Parcel(plot_number=plot_number, area_sqm=100)
        session.add_all([member, parcel])
        await session.flush()
        session.add(MemberParcel(member_id=member.id, parcel_id=parcel.id, is_invoice_address=True))
        await session.commit()

    r_create = await client.post("/finances/runs", data={
        "year": str(year), "subject": "Pay from run test", "issued_date": f"{year}-08-01",
        "due_date": f"{year}-09-01", "footer_text": "",
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
        return run_id, invoice.id


async def web_login(client, email: str = "admin@example.com", password: str = "testpasswort123") -> None:
    response = await client.post("/auth/login", data={"email": email, "password": password})
    assert response.status_code in (302, 303)


async def test_run_detail_shows_payment_status_and_button(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    run_id, invoice_id = await _make_run_with_invoice(client)

    response = await client.get(f"/finances/runs/{run_id}")
    assert response.status_code == 200
    assert f"payment-modal-{invoice_id}" in response.text
    assert "PAYFROMRUN" in response.text


async def test_recording_payment_from_run_page_redirects_back_to_run(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    run_id, invoice_id = await _make_run_with_invoice(client)

    async with AsyncSessionLocal() as session:
        session.add(FinanceAccount(name="Vereinskonto", account_type=FinanceAccountType.BANK, is_active=True))
        await session.commit()
        result = await session.execute(select(FinanceAccount).where(FinanceAccount.name == "Vereinskonto"))
        account = result.scalar_one()

    response = await client.post(
        f"/finances/invoices/{invoice_id}/payments",
        data={
            "amount": "50.00", "paid_on": "2026-08-15", "note": "Bank transfer",
            "account_id": account.id, "from_run": run_id,
        },
    )
    assert response.status_code in (302, 303)
    assert response.headers["location"] == f"/finances/runs/{run_id}"

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Invoice).options(selectinload(Invoice.payments), selectinload(Invoice.reminders))
            .where(Invoice.id == invoice_id)
        )
        invoice = result.scalar_one()
        assert invoice.payment_status == "paid"
        payments_result = await session.execute(select(InvoicePayment).where(InvoicePayment.invoice_id == invoice_id))
        payment = payments_result.scalar_one()
        assert float(payment.amount) == 50.0
        assert payment.account_id == account.id


async def test_partial_payment_from_run_page_shows_partially_paid_status(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    run_id, invoice_id = await _make_run_with_invoice(client)

    response = await client.post(
        f"/finances/invoices/{invoice_id}/payments",
        data={"amount": "20.00", "paid_on": "2026-08-15", "note": "", "account_id": "", "from_run": run_id},
    )
    assert response.status_code in (302, 303)
    assert response.headers["location"] == f"/finances/runs/{run_id}"

    run_page = await client.get(f"/finances/runs/{run_id}")
    assert run_page.status_code == 200

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Invoice).options(selectinload(Invoice.payments), selectinload(Invoice.reminders))
            .where(Invoice.id == invoice_id)
        )
        invoice = result.scalar_one()
        assert invoice.payment_status == "partially_paid"


async def test_deleting_payment_from_run_page_redirects_back_to_run(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    run_id, invoice_id = await _make_run_with_invoice(client)

    await client.post(
        f"/finances/invoices/{invoice_id}/payments",
        data={"amount": "50.00", "paid_on": "2026-08-15", "note": "", "account_id": "", "from_run": run_id},
    )
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(InvoicePayment).where(InvoicePayment.invoice_id == invoice_id))
        payment = result.scalar_one()

    response = await client.post(
        f"/finances/invoices/{invoice_id}/payments/{payment.id}/delete",
        data={"from_run": run_id},
    )
    assert response.status_code in (302, 303)
    assert response.headers["location"] == f"/finances/runs/{run_id}"

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(InvoicePayment).where(InvoicePayment.invoice_id == invoice_id))
        assert result.scalar_one_or_none() is None


async def test_payment_without_from_run_still_redirects_to_invoice_detail(client, admin_user):
    """Unchanged, existing behavior when posted from the invoice's own
    detail page (no from_run field at all)."""
    await web_login(client)
    await _enable_finances_module()
    run_id, invoice_id = await _make_run_with_invoice(client)

    response = await client.post(
        f"/finances/invoices/{invoice_id}/payments",
        data={"amount": "50.00", "paid_on": "2026-08-15", "note": "", "account_id": ""},
    )
    assert response.status_code in (302, 303)
    assert response.headers["location"] == f"/finances/invoices/{invoice_id}"
