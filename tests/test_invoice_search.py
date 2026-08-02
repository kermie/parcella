"""
Issue #190: search/filter outgoing and incoming invoices by invoice
number, recipient/sender, amount, and payment status.
"""
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import ClubSetting, Invoice, IncomingInvoice, Member, MemberParcel, Parcel


async def web_login(client, email: str = "admin@example.com", password: str = "testpasswort123") -> None:
    response = await client.post("/auth/login", data={"email": email, "password": password})
    assert response.status_code in (302, 303)


async def _enable_finances_module():
    async with AsyncSessionLocal() as session:
        session.add(ClubSetting(key="modul_finances", value="true", description="test"))
        await session.commit()


async def _make_invoice(client, plot_number, unit_price, year="2026"):
    async with AsyncSessionLocal() as session:
        member = Member(
            first_name="Search", last_name=plot_number,
            street="Gartenweg 1", postal_code="12345", city="Testort",
        )
        parcel = Parcel(plot_number=plot_number, area_sqm=100)
        session.add_all([member, parcel])
        await session.flush()
        session.add(MemberParcel(member_id=member.id, parcel_id=parcel.id, is_invoice_address=True))
        await session.commit()
        parcel_id = parcel.id

    r_create = await client.post("/finances/runs", data={
        "year": year, "subject": "Search test", "issued_date": f"{year}-01-01",
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
        return result.scalar_one(), run_id


async def test_outgoing_invoice_search_by_recipient(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    await _make_invoice(client, "SEARCH-A", "50.00")
    await _make_invoice(client, "SEARCH-B", "60.00")

    response = await client.get("/finances/invoices?recipient=SEARCH-A")
    assert response.status_code == 200
    assert "SEARCH-A" in response.text
    assert "SEARCH-B" not in response.text


async def test_outgoing_invoice_search_by_amount_range(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    await _make_invoice(client, "SEARCH-C", "77.00")
    await _make_invoice(client, "SEARCH-D", "999.00")

    response = await client.get("/finances/invoices?amount_min=70&amount_max=80")
    assert response.status_code == 200
    assert "SEARCH-C" in response.text
    assert "SEARCH-D" not in response.text


async def test_outgoing_invoice_search_by_status(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    invoice, _run_id = await _make_invoice(client, "SEARCH-E", "40.00")
    await client.post(f"/finances/invoices/{invoice.id}/payments", data={
        "amount": "40.00", "paid_on": "2026-03-01", "note": "",
    })
    await _make_invoice(client, "SEARCH-F", "40.00")

    response = await client.get("/finances/invoices?status=paid")
    assert response.status_code == 200
    assert "SEARCH-E" in response.text
    assert "SEARCH-F" not in response.text


async def test_incoming_invoice_search_by_sender(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    await client.post("/finances/incoming-invoices", data={
        "sender": "Findable Vendor Ltd", "invoice_number": "", "invoice_date": "2026-04-01", "note": "",
        "category_id": [""], "amount": ["12.00"],
    })
    await client.post("/finances/incoming-invoices", data={
        "sender": "Other Vendor Inc", "invoice_number": "", "invoice_date": "2026-04-01", "note": "",
        "category_id": [""], "amount": ["13.00"],
    })

    response = await client.get("/finances/incoming-invoices?sender=Findable")
    assert response.status_code == 200
    assert "Findable Vendor Ltd" in response.text
    assert "Other Vendor Inc" not in response.text


async def test_incoming_invoice_search_by_invoice_number(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    await client.post("/finances/incoming-invoices", data={
        "sender": "Number Test Sender", "invoice_number": "RE-2026-042", "invoice_date": "2026-04-02", "note": "",
        "category_id": [""], "amount": ["15.00"],
    })
    await client.post("/finances/incoming-invoices", data={
        "sender": "Other Number Sender", "invoice_number": "RE-2026-099", "invoice_date": "2026-04-02", "note": "",
        "category_id": [""], "amount": ["16.00"],
    })

    response = await client.get("/finances/incoming-invoices?invoice_number=042")
    assert response.status_code == 200
    assert "Number Test Sender" in response.text
    assert "Other Number Sender" not in response.text


async def test_incoming_invoice_search_by_amount_range(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    await client.post("/finances/incoming-invoices", data={
        "sender": "Amount Range Sender", "invoice_number": "", "invoice_date": "2026-04-03", "note": "",
        "category_id": [""], "amount": ["25.00"],
    })
    await client.post("/finances/incoming-invoices", data={
        "sender": "Out Of Range Sender", "invoice_number": "", "invoice_date": "2026-04-03", "note": "",
        "category_id": [""], "amount": ["500.00"],
    })

    response = await client.get("/finances/incoming-invoices?amount_min=20&amount_max=30")
    assert response.status_code == 200
    assert "Amount Range Sender" in response.text
    assert "Out Of Range Sender" not in response.text


async def test_incoming_invoice_search_by_status(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    await client.post("/finances/incoming-invoices", data={
        "sender": "Paid Status Sender", "invoice_number": "", "invoice_date": "2026-04-04", "note": "",
        "category_id": [""], "amount": ["18.00"],
    })
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(IncomingInvoice).where(IncomingInvoice.sender == "Paid Status Sender"))
        incoming_invoice_id = result.scalar_one().id
    await client.post(f"/finances/incoming-invoices/{incoming_invoice_id}/payments", data={
        "amount": "18.00", "paid_on": "2026-04-05", "note": "",
    })

    await client.post("/finances/incoming-invoices", data={
        "sender": "Open Status Sender", "invoice_number": "", "invoice_date": "2026-04-04", "note": "",
        "category_id": [""], "amount": ["19.00"],
    })

    response = await client.get("/finances/incoming-invoices?status=paid")
    assert response.status_code == 200
    assert "Paid Status Sender" in response.text
    assert "Open Status Sender" not in response.text


async def test_run_detail_search_by_recipient(client, admin_user):
    """Issue #190 follow-up: the flat /finances/invoices list got
    filters, but a single run's own invoice table had none at all."""
    await web_login(client)
    await _enable_finances_module()

    async def _make_parcel(plot_number):
        async with AsyncSessionLocal() as session:
            member = Member(
                first_name="Search", last_name=plot_number,
                street="Gartenweg 1", postal_code="12345", city="Testort",
            )
            parcel = Parcel(plot_number=plot_number, area_sqm=100)
            session.add_all([member, parcel])
            await session.flush()
            session.add(MemberParcel(member_id=member.id, parcel_id=parcel.id, is_invoice_address=True))
            await session.commit()
            return parcel.id

    parcel_a_id = await _make_parcel("RUNSEARCH-A")
    parcel_b_id = await _make_parcel("RUNSEARCH-B")

    r_create = await client.post("/finances/runs", data={
        "year": "2027", "subject": "Run search test", "issued_date": "2027-01-01",
        "due_date": "2027-02-01", "footer_text": "",
    })
    assert r_create.status_code in (302, 303)
    run_id = r_create.headers["location"].rstrip("/").split("/")[-1]

    for order, parcel_id in enumerate([parcel_a_id, parcel_b_id]):
        r_item = await client.post(f"/finances/runs/{run_id}/items", data={
            "order_number": str(order), "name": "Fee", "description": "",
            "pricing_mode": "fixed_per_parcel", "unit_price": "50.00",
            "parcel_ids": [parcel_id],
        })
        assert r_item.status_code in (302, 303)

    r_finalize = await client.post(f"/finances/runs/{run_id}/finalize")
    assert r_finalize.status_code in (302, 303)

    response = await client.get(f"/finances/runs/{run_id}?recipient=RUNSEARCH-A")
    assert response.status_code == 200
    assert "RUNSEARCH-A" in response.text
    assert "RUNSEARCH-B" not in response.text


async def test_run_detail_search_by_amount_and_status(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    invoice, run_id = await _make_invoice(client, "RUNSEARCH-C", "70.00", year="2028")
    await client.post(f"/finances/invoices/{invoice.id}/payments", data={
        "amount": "70.00", "paid_on": "2028-03-01", "note": "",
    })

    response = await client.get(f"/finances/runs/{run_id}?amount_min=60&amount_max=80&status=paid")
    assert response.status_code == 200
    assert "RUNSEARCH-C" in response.text

    response_wrong_status = await client.get(f"/finances/runs/{run_id}?status=open")
    assert "RUNSEARCH-C" not in response_wrong_status.text
