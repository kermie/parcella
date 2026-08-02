"""
Issue #181: mark incoming invoices as paid, same method as outgoing
invoices -- IncomingInvoicePayment mirrors InvoicePayment (amount,
paid_on, optional note/account), supports partial payments, and drives
IncomingInvoice.payment_status the same way Invoice.payment_status
works (minus the reminder-fee concept incoming invoices don't have).
"""
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import AsyncSessionLocal
from app.models import ClubSetting, FinanceAccount, FinanceAccountType, IncomingInvoice, IncomingInvoicePayment


async def web_login(client, email: str = "admin@example.com", password: str = "testpasswort123") -> None:
    response = await client.post("/auth/login", data={"email": email, "password": password})
    assert response.status_code in (302, 303)


async def _enable_finances_module():
    async with AsyncSessionLocal() as session:
        session.add(ClubSetting(key="modul_finances", value="true", description="test"))
        await session.commit()


async def _make_incoming_invoice(client, sender, amount, invoice_date="2036-01-15"):
    response = await client.post("/finances/incoming-invoices", data={
        "sender": sender, "invoice_number": "", "invoice_date": invoice_date, "note": "",
        "category_id": [""], "amount": [amount],
    })
    assert response.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(IncomingInvoice).options(selectinload(IncomingInvoice.payments))
            .where(IncomingInvoice.sender == sender)
        )
        return result.scalar_one()


async def test_incoming_invoice_starts_open(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    invoice = await _make_incoming_invoice(client, "Open Test", "100.00")
    assert invoice.payment_status == "open"


async def test_partial_then_full_payment_updates_status(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    invoice = await _make_incoming_invoice(client, "Partial Test", "100.00")

    r1 = await client.post(f"/finances/incoming-invoices/{invoice.id}/payments", data={
        "amount": "40.00", "paid_on": "2036-02-01", "note": "First installment",
    })
    assert r1.status_code in (302, 303)
    assert r1.headers["location"] == f"/finances/incoming-invoices/{invoice.id}"

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(IncomingInvoice)
            .options(selectinload(IncomingInvoice.payments), selectinload(IncomingInvoice.line_items))
            .where(IncomingInvoice.id == invoice.id)
        )
        refreshed = result.scalar_one()
        assert refreshed.payment_status == "partially_paid"
        assert refreshed.paid_total == 40.0

    r2 = await client.post(f"/finances/incoming-invoices/{invoice.id}/payments", data={
        "amount": "60.00", "paid_on": "2036-02-15", "note": "",
    })
    assert r2.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(IncomingInvoice)
            .options(selectinload(IncomingInvoice.payments), selectinload(IncomingInvoice.line_items))
            .where(IncomingInvoice.id == invoice.id)
        )
        refreshed = result.scalar_one()
        assert refreshed.payment_status == "paid"
        assert refreshed.paid_total == 100.0


async def test_payment_can_be_tagged_with_account(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    invoice = await _make_incoming_invoice(client, "Account Test", "50.00")

    async with AsyncSessionLocal() as session:
        session.add(FinanceAccount(name="Vereinskonto", account_type=FinanceAccountType.BANK, is_active=True))
        await session.commit()
        result = await session.execute(select(FinanceAccount).where(FinanceAccount.name == "Vereinskonto"))
        account = result.scalar_one()

    await client.post(f"/finances/incoming-invoices/{invoice.id}/payments", data={
        "amount": "50.00", "paid_on": "2036-03-01", "note": "", "account_id": account.id,
    })

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(IncomingInvoicePayment).where(IncomingInvoicePayment.incoming_invoice_id == invoice.id)
        )
        payment = result.scalar_one()
        assert payment.account_id == account.id


async def test_deleting_a_payment_reverts_status(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    invoice = await _make_incoming_invoice(client, "Delete Payment Test", "30.00")

    await client.post(f"/finances/incoming-invoices/{invoice.id}/payments", data={
        "amount": "30.00", "paid_on": "2036-04-01", "note": "",
    })

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(IncomingInvoicePayment).where(IncomingInvoicePayment.incoming_invoice_id == invoice.id)
        )
        payment = result.scalar_one()

    r_delete = await client.post(f"/finances/incoming-invoices/{invoice.id}/payments/{payment.id}/delete")
    assert r_delete.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(IncomingInvoice)
            .options(selectinload(IncomingInvoice.payments), selectinload(IncomingInvoice.line_items))
            .where(IncomingInvoice.id == invoice.id)
        )
        refreshed = result.scalar_one()
        assert refreshed.payment_status == "open"
        assert refreshed.paid_total == 0.0


async def test_incoming_invoices_list_shows_payment_status_badge(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    invoice = await _make_incoming_invoice(client, "Badge Test", "20.00")
    await client.post(f"/finances/incoming-invoices/{invoice.id}/payments", data={
        "amount": "20.00", "paid_on": "2036-05-01", "note": "",
    })

    response = await client.get("/finances/incoming-invoices")
    assert response.status_code == 200
    assert "UndefinedError" not in response.text


async def test_incoming_invoice_detail_shows_payment_form_and_history(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    invoice = await _make_incoming_invoice(client, "Detail Payment Test", "15.00")
    await client.post(f"/finances/incoming-invoices/{invoice.id}/payments", data={
        "amount": "15.00", "paid_on": "2036-06-01", "note": "Paid in full",
    })

    response = await client.get(f"/finances/incoming-invoices/{invoice.id}")
    assert response.status_code == 200
    assert "UndefinedError" not in response.text
    assert "Paid in full" in response.text


async def test_deleting_incoming_invoice_cascades_payments(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    invoice = await _make_incoming_invoice(client, "Cascade Test", "10.00")
    await client.post(f"/finances/incoming-invoices/{invoice.id}/payments", data={
        "amount": "10.00", "paid_on": "2036-07-01", "note": "",
    })

    await client.post(f"/finances/incoming-invoices/{invoice.id}/delete")

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(IncomingInvoicePayment).where(IncomingInvoicePayment.incoming_invoice_id == invoice.id)
        )
        assert result.scalar_one_or_none() is None
