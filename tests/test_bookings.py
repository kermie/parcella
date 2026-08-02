"""
Issue #180: a unified, filterable, club-wide list of every invoice
payment (incoming and outgoing), sorted by date descending -- see
app/bookings.py for why this is built in Python rather than a SQL
UNION ALL (a payment can touch several categories via its invoice's
line items).
"""
from sqlalchemy import select

from app.bookings import list_bookings
from app.database import AsyncSessionLocal
from app.models import ClubSetting, FinanceCategoryGroup, FinanceCategory, IncomingInvoice, Invoice, Member, MemberParcel, Parcel


async def web_login(client, email: str = "admin@example.com", password: str = "testpasswort123") -> None:
    response = await client.post("/auth/login", data={"email": email, "password": password})
    assert response.status_code in (302, 303)


async def _enable_finances_module():
    async with AsyncSessionLocal() as session:
        session.add(ClubSetting(key="modul_finances", value="true", description="test"))
        await session.commit()


async def _make_category(code, title, group=FinanceCategoryGroup.INCOME) -> str:
    async with AsyncSessionLocal() as session:
        category = FinanceCategory(code=code, title=title, group=group)
        session.add(category)
        await session.commit()
        return category.id


async def _make_run_with_invoice(client, plot_number, year, item_defs):
    async with AsyncSessionLocal() as session:
        member = Member(
            first_name="Book", last_name=plot_number,
            street="Gartenweg 1", postal_code="12345", city="Testort",
        )
        parcel = Parcel(plot_number=plot_number, area_sqm=100)
        session.add_all([member, parcel])
        await session.flush()
        session.add(MemberParcel(member_id=member.id, parcel_id=parcel.id, is_invoice_address=True))
        await session.commit()

    r_create = await client.post("/finances/runs", data={
        "year": str(year), "subject": "Bookings test", "issued_date": f"{year}-01-01",
        "due_date": f"{year}-02-01", "footer_text": "",
    })
    assert r_create.status_code in (302, 303)
    run_id = r_create.headers["location"].rstrip("/").split("/")[-1]

    for order, (name, unit_price, category_id) in enumerate(item_defs):
        r_item = await client.post(f"/finances/runs/{run_id}/items", data={
            "order_number": str(order), "name": name, "description": "",
            "pricing_mode": "fixed_per_parcel", "unit_price": unit_price,
            "applies_to_all_parcels": "on", "category_id": category_id or "",
        })
        assert r_item.status_code in (302, 303)

    r_finalize = await client.post(f"/finances/runs/{run_id}/finalize")
    assert r_finalize.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Invoice).where(Invoice.invoice_run_id == run_id))
        return result.scalar_one().id


async def _record_payment(client, invoice_id, amount, paid_on):
    response = await client.post(f"/finances/invoices/{invoice_id}/payments", data={
        "amount": amount, "paid_on": paid_on, "note": "",
    })
    assert response.status_code in (302, 303)


async def _make_incoming_invoice(client, sender, invoice_date, category_id, amount):
    response = await client.post("/finances/incoming-invoices", data={
        "sender": sender, "invoice_number": "", "invoice_date": invoice_date, "note": "",
        "category_id": [category_id], "amount": [amount],
    })
    assert response.status_code in (302, 303)
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(IncomingInvoice).where(IncomingInvoice.sender == sender))
        return result.scalar_one().id


async def _record_incoming_payment(client, incoming_invoice_id, amount, paid_on):
    response = await client.post(f"/finances/incoming-invoices/{incoming_invoice_id}/payments", data={
        "amount": amount, "paid_on": paid_on, "note": "",
    })
    assert response.status_code in (302, 303)


async def test_bookings_combine_income_and_expense_sorted_by_date_desc(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    income_cat = await _make_category("40500", "Dues Booking Test")
    expense_cat = await _make_category("50400", "Repairs Booking Test", group=FinanceCategoryGroup.EXPENSE)

    invoice_id = await _make_run_with_invoice(client, "BOOK-A", 2040, [("Dues", "50.00", income_cat)])
    await _record_payment(client, invoice_id, "50.00", "2040-03-01")

    incoming_id = await _make_incoming_invoice(client, "Repair Shop", "2040-02-01", expense_cat, "20.00")
    await _record_incoming_payment(client, incoming_id, "20.00", "2040-04-01")

    async with AsyncSessionLocal() as session:
        rows = await list_bookings(session)

    dates = [r.booking_date.isoformat() for r in rows if r.booking_date.year == 2040]
    assert dates == sorted(dates, reverse=True)
    directions = {r.direction for r in rows if r.booking_date.year == 2040}
    assert directions == {"income", "expense"}


async def test_bookings_filter_by_direction(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    expense_cat = await _make_category("50500", "Direction Test", group=FinanceCategoryGroup.EXPENSE)
    incoming_id = await _make_incoming_invoice(client, "Direction Sender", "2041-01-01", expense_cat, "30.00")
    await _record_incoming_payment(client, incoming_id, "30.00", "2041-01-15")

    async with AsyncSessionLocal() as session:
        income_only = await list_bookings(session, direction="income")
        expense_only = await list_bookings(session, direction="expense")

    assert not any(r.counterparty == "Direction Sender" for r in income_only)
    assert any(r.counterparty == "Direction Sender" for r in expense_only)


async def test_bookings_filter_by_category(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    cat_a = await _make_category("50600", "Category Filter A", group=FinanceCategoryGroup.EXPENSE)
    cat_b = await _make_category("50700", "Category Filter B", group=FinanceCategoryGroup.EXPENSE)

    incoming_a = await _make_incoming_invoice(client, "Vendor A", "2042-01-01", cat_a, "10.00")
    await _record_incoming_payment(client, incoming_a, "10.00", "2042-01-05")
    incoming_b = await _make_incoming_invoice(client, "Vendor B", "2042-01-01", cat_b, "15.00")
    await _record_incoming_payment(client, incoming_b, "15.00", "2042-01-05")

    async with AsyncSessionLocal() as session:
        rows = await list_bookings(session, category_id=cat_a)

    counterparties = {r.counterparty for r in rows}
    assert "Vendor A" in counterparties
    assert "Vendor B" not in counterparties


async def test_bookings_filter_by_search(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    cat = await _make_category("50800", "Search Test", group=FinanceCategoryGroup.EXPENSE)
    incoming_id = await _make_incoming_invoice(client, "Findable Sender Ltd", "2043-01-01", cat, "5.00")
    await _record_incoming_payment(client, incoming_id, "5.00", "2043-01-10")

    async with AsyncSessionLocal() as session:
        matches = await list_bookings(session, search="findable")
        no_matches = await list_bookings(session, search="nonexistent-search-term")

    assert any(r.counterparty == "Findable Sender Ltd" for r in matches)
    assert not any(r.counterparty == "Findable Sender Ltd" for r in no_matches)


async def test_bookings_filter_by_amount_range(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    cat = await _make_category("50900", "Amount Test", group=FinanceCategoryGroup.EXPENSE)
    incoming_id = await _make_incoming_invoice(client, "Amount Sender", "2044-01-01", cat, "500.00")
    await _record_incoming_payment(client, incoming_id, "500.00", "2044-01-05")

    async with AsyncSessionLocal() as session:
        in_range = await list_bookings(session, amount_min=100, amount_max=1000)
        out_of_range = await list_bookings(session, amount_min=600, amount_max=1000)

    assert any(r.counterparty == "Amount Sender" for r in in_range)
    assert not any(r.counterparty == "Amount Sender" for r in out_of_range)


async def test_bookings_filter_by_date_range(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    cat = await _make_category("51000", "Date Test", group=FinanceCategoryGroup.EXPENSE)
    incoming_id = await _make_incoming_invoice(client, "Date Sender", "2045-01-01", cat, "12.00")
    await _record_incoming_payment(client, incoming_id, "12.00", "2045-06-15")

    async with AsyncSessionLocal() as session:
        from datetime import date
        in_range = await list_bookings(session, date_from=date(2045, 6, 1), date_to=date(2045, 6, 30))
        out_of_range = await list_bookings(session, date_from=date(2045, 7, 1), date_to=date(2045, 12, 31))

    assert any(r.counterparty == "Date Sender" for r in in_range)
    assert not any(r.counterparty == "Date Sender" for r in out_of_range)


async def test_bookings_page_renders(client, admin_user):
    await web_login(client)
    await _enable_finances_module()

    response = await client.get("/finances/bookings")
    assert response.status_code == 200
    assert "UndefinedError" not in response.text


async def test_bookings_page_renders_with_filters_applied(client, admin_user):
    await web_login(client)
    await _enable_finances_module()

    response = await client.get("/finances/bookings?search=test&direction=expense&amount_min=1&amount_max=100")
    assert response.status_code == 200
    assert "UndefinedError" not in response.text
