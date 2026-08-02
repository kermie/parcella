"""
Issue #179: a cash-based accounting statement for the tax office,
broken down by FinanceCategory. Expenses come from
IncomingInvoiceLineItem (already categorized, issue #178); income has
no category of its own, so a payment's amount is split proportionally
across its invoice's line-item categories -- see
docs/ADR/0060-cash-accounting-statement-income-categorization.md.
"""
from sqlalchemy import select

from app.accounting_statement import compute_cash_accounting_statement, available_statement_years
from app.database import AsyncSessionLocal
from app.models import (
    ClubSetting, FinanceCategory, FinanceCategoryGroup, Invoice,
    Member, MemberParcel, Parcel,
)


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
    """item_defs: list of (name, unit_price, category_id_or_None). Creates one
    parcel/member, one run, one FIXED_PER_PARCEL item per entry, and
    finalizes -- returns (run_id, invoice_id)."""
    async with AsyncSessionLocal() as session:
        member = Member(
            first_name="Stmt", last_name=plot_number,
            street="Gartenweg 1", postal_code="12345", city="Testort",
        )
        parcel = Parcel(plot_number=plot_number, area_sqm=100)
        session.add_all([member, parcel])
        await session.flush()
        session.add(MemberParcel(member_id=member.id, parcel_id=parcel.id, is_invoice_address=True))
        await session.commit()

    r_create = await client.post("/finances/runs", data={
        "year": str(year), "subject": "Statement test", "issued_date": f"{year}-01-01",
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
    assert r_finalize.status_code in (302, 303), r_finalize.headers.get("location")

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Invoice).where(Invoice.invoice_run_id == run_id))
        invoice = result.scalar_one()
        return run_id, invoice.id


async def _record_payment(client, invoice_id, amount, paid_on):
    response = await client.post(f"/finances/invoices/{invoice_id}/payments", data={
        "amount": amount, "paid_on": paid_on, "note": "",
    })
    assert response.status_code in (302, 303)


def _amount_for(statement_rows, category_title):
    for row in statement_rows:
        if row.category and row.category.title == category_title:
            return row.amount
    return None


def _uncategorized_amount(statement_rows):
    for row in statement_rows:
        if row.category is None:
            return row.amount
    return None


async def test_income_split_proportionally_across_categories(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    cat_a = await _make_category("40100", "Membership fees")
    cat_b = await _make_category("40200", "Usage fees")

    _, invoice_id = await _make_run_with_invoice(client, "STMT-A", 2031, [
        ("Membership", "100.00", cat_a),
        ("Usage", "200.00", cat_b),
    ])
    await _record_payment(client, invoice_id, "150.00", "2031-03-01")

    async with AsyncSessionLocal() as session:
        statement = await compute_cash_accounting_statement(session, 2031)

    assert _amount_for(statement.income_by_category, "Membership fees") == 50.0
    assert _amount_for(statement.income_by_category, "Usage fees") == 100.0
    assert statement.income_total == 150.0


async def test_income_uncategorized_when_item_has_no_category(client, admin_user):
    await web_login(client)
    await _enable_finances_module()

    _, invoice_id = await _make_run_with_invoice(client, "STMT-B", 2032, [
        ("Flat fee", "80.00", None),
    ])
    await _record_payment(client, invoice_id, "80.00", "2032-05-01")

    async with AsyncSessionLocal() as session:
        statement = await compute_cash_accounting_statement(session, 2032)

    assert _uncategorized_amount(statement.income_by_category) == 80.0


async def test_income_excludes_payments_from_other_years(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    cat = await _make_category("40300", "Other income")

    _, invoice_id = await _make_run_with_invoice(client, "STMT-C", 2033, [
        ("Fee", "60.00", cat),
    ])
    await _record_payment(client, invoice_id, "60.00", "2034-01-15")

    async with AsyncSessionLocal() as session:
        statement_2033 = await compute_cash_accounting_statement(session, 2033)
        statement_2034 = await compute_cash_accounting_statement(session, 2034)

    assert _amount_for(statement_2033.income_by_category, "Other income") is None
    assert statement_2033.income_total == 0.0
    assert _amount_for(statement_2034.income_by_category, "Other income") == 60.0


async def test_expenses_grouped_by_category_and_year_filtered(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    cat = await _make_category("50100", "Maintenance", group=FinanceCategoryGroup.EXPENSE)

    await client.post("/finances/incoming-invoices", data={
        "sender": "Repair Co", "invoice_number": "", "invoice_date": "2035-06-01", "note": "",
        "category_id": [cat], "amount": ["45.00"],
    })
    await client.post("/finances/incoming-invoices", data={
        "sender": "Repair Co", "invoice_number": "", "invoice_date": "2036-06-01", "note": "",
        "category_id": [cat], "amount": ["999.00"],
    })

    async with AsyncSessionLocal() as session:
        statement = await compute_cash_accounting_statement(session, 2035)

    assert _amount_for(statement.expense_by_category, "Maintenance") == 45.0
    assert statement.expense_total == 45.0


async def test_net_result_is_income_minus_expenses(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    income_cat = await _make_category("40400", "Dues")
    expense_cat = await _make_category("50200", "Supplies", group=FinanceCategoryGroup.EXPENSE)

    _, invoice_id = await _make_run_with_invoice(client, "STMT-D", 2037, [
        ("Dues", "200.00", income_cat),
    ])
    await _record_payment(client, invoice_id, "200.00", "2037-04-01")
    await client.post("/finances/incoming-invoices", data={
        "sender": "Supplier", "invoice_number": "", "invoice_date": "2037-05-01", "note": "",
        "category_id": [expense_cat], "amount": ["75.00"],
    })

    async with AsyncSessionLocal() as session:
        statement = await compute_cash_accounting_statement(session, 2037)

    assert statement.income_total == 200.0
    assert statement.expense_total == 75.0
    assert statement.net_result == 125.0


async def test_accounting_statement_page_renders(client, admin_user):
    await web_login(client)
    await _enable_finances_module()

    response = await client.get("/finances/accounting-statement")
    assert response.status_code == 200
    assert "UndefinedError" not in response.text


async def test_accounting_statement_pdf_export(client, admin_user):
    await web_login(client)
    await _enable_finances_module()

    response = await client.get("/finances/accounting-statement/pdf?year=2030")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content[:4] == b"%PDF"


async def test_available_statement_years_includes_current_year_even_when_empty(client, admin_user):
    async with AsyncSessionLocal() as session:
        from datetime import date
        years = await available_statement_years(session)
        assert date.today().year in years
