"""
Issue #174: a unified, filterable, CSV-exportable/importable list of
everything booked against a FinanceAccount -- InvoicePayment rows
(always tied to an invoice) UNION ALL'd with the new AccountTransaction
rows (anything else: refunds, purchases, bank fees, CSV-imported).
Confirmed with the reporter this deliberately reopens FinanceAccount's
original "not a ledger" stance from issue #156 -- see ADR 0059.
"""
import base64
import io
from datetime import date

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import (
    ClubSetting, Parcel, Member, MemberParcel, Invoice, InvoicePayment,
    FinanceAccount, FinanceAccountType, AccountTransaction,
)


async def web_login(client, email: str = "admin@example.com", password: str = "testpasswort123") -> None:
    response = await client.post("/auth/login", data={"email": email, "password": password})
    assert response.status_code in (302, 303)


async def _enable_finances_module():
    async with AsyncSessionLocal() as session:
        session.add(ClubSetting(key="modul_finances", value="true", description="test"))
        await session.commit()


async def _make_account(name="Vereinskonto") -> str:
    async with AsyncSessionLocal() as session:
        account = FinanceAccount(name=name, account_type=FinanceAccountType.BANK, is_active=True)
        session.add(account)
        await session.commit()
        return account.id


async def _make_invoice_payment(client, account_id: str, amount: str, paid_on: str, plot_number: str) -> None:
    """Creates a finalized invoice and records a payment against it,
    tagged to account_id."""
    async with AsyncSessionLocal() as session:
        member = Member(
            first_name="Booking", last_name=plot_number,
            street="Gartenweg 1", postal_code="12345", city="Testort",
        )
        parcel = Parcel(plot_number=plot_number, area_sqm=100)
        session.add_all([member, parcel])
        await session.flush()
        session.add(MemberParcel(member_id=member.id, parcel_id=parcel.id, is_invoice_address=True))
        await session.commit()

    r_create = await client.post("/finances/runs", data={
        "year": "2026", "subject": "Booking test", "issued_date": "2026-08-01",
        "due_date": "2026-09-01", "footer_text": "",
    })
    assert r_create.status_code in (302, 303)
    run_id = r_create.headers["location"].rstrip("/").split("/")[-1]

    r_item = await client.post(f"/finances/runs/{run_id}/items", data={
        "order_number": "10", "name": "Fee", "description": "",
        "pricing_mode": "fixed_per_parcel", "unit_price": amount,
        "applies_to_all_parcels": "on",
    })
    assert r_item.status_code in (302, 303)

    r_finalize = await client.post(f"/finances/runs/{run_id}/finalize")
    assert r_finalize.status_code in (302, 303), r_finalize.headers.get("location")

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Invoice).where(Invoice.invoice_run_id == run_id))
        invoice = result.scalar_one()

    r_pay = await client.post(f"/finances/invoices/{invoice.id}/payments", data={
        "amount": amount, "paid_on": paid_on, "note": "", "account_id": account_id,
    })
    assert r_pay.status_code in (302, 303)


async def test_bookings_list_unifies_invoice_payments_and_transactions(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    account_id = await _make_account()

    await _make_invoice_payment(client, account_id, "50.00", "2026-08-10", "BOOK-1")
    async with AsyncSessionLocal() as session:
        session.add(AccountTransaction(
            account_id=account_id, booking_date=date.fromisoformat("2026-08-15"), amount="-12.50",
            description="Bank fee", source="manual",
        ))
        await session.commit()

    response = await client.get(f"/finances/accounts/{account_id}/bookings")
    assert response.status_code == 200
    assert "Bank fee" in response.text
    assert "50,00" in response.text
    # Most recent first: the bank fee (Aug 15) must appear before the invoice payment (Aug 10).
    assert response.text.index("Bank fee") < response.text.index("50,00")


async def test_bookings_search_filters_by_description(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    account_id = await _make_account()

    async with AsyncSessionLocal() as session:
        session.add(AccountTransaction(
            account_id=account_id, booking_date=date.fromisoformat("2026-08-01"), amount="-5.00",
            description="Office supplies", source="manual",
        ))
        session.add(AccountTransaction(
            account_id=account_id, booking_date=date.fromisoformat("2026-08-02"), amount="-8.00",
            description="Postage stamps", source="manual",
        ))
        await session.commit()

    response = await client.get(f"/finances/accounts/{account_id}/bookings?search=Office")
    assert response.status_code == 200
    assert "Office supplies" in response.text
    assert "Postage stamps" not in response.text


async def test_bookings_date_range_filter(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    account_id = await _make_account()

    async with AsyncSessionLocal() as session:
        session.add(AccountTransaction(
            account_id=account_id, booking_date=date.fromisoformat("2026-01-01"), amount="-1.00",
            description="January entry", source="manual",
        ))
        session.add(AccountTransaction(
            account_id=account_id, booking_date=date.fromisoformat("2026-08-01"), amount="-2.00",
            description="August entry", source="manual",
        ))
        await session.commit()

    response = await client.get(f"/finances/accounts/{account_id}/bookings?date_from=2026-07-01")
    assert response.status_code == 200
    assert "August entry" in response.text
    assert "January entry" not in response.text


async def test_bookings_search_matches_counterparty(client, admin_user):
    """Issue #185: "I want to know who send me the money or who
    received this money from me. I want to filter for this sender or
    recipient" -- counterparty is searchable via the same free-text box
    as reference/description."""
    await web_login(client)
    await _enable_finances_module()
    account_id = await _make_account()

    async with AsyncSessionLocal() as session:
        session.add(AccountTransaction(
            account_id=account_id, booking_date=date.fromisoformat("2026-08-01"), amount="-1.00",
            description="Bank fee", counterparty="Sparkasse Musterstadt", source="manual",
        ))
        session.add(AccountTransaction(
            account_id=account_id, booking_date=date.fromisoformat("2026-08-02"), amount="-2.00",
            description="Refund", counterparty="Gartenbau Müller", source="manual",
        ))
        await session.commit()

    response = await client.get(f"/finances/accounts/{account_id}/bookings?search=Sparkasse")
    assert response.status_code == 200
    assert "Sparkasse Musterstadt" in response.text
    assert "Gartenbau Müller" not in response.text


async def test_bookings_manual_add_with_counterparty(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    account_id = await _make_account()

    response = await client.post(f"/finances/accounts/{account_id}/bookings/manual", data={
        "booking_date": "2026-08-03", "amount": "-15.00", "description": "Repair",
        "counterparty": "Repair Shop GmbH",
    })
    assert response.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AccountTransaction).where(AccountTransaction.account_id == account_id)
        )
        transaction = result.scalar_one()
        assert transaction.counterparty == "Repair Shop GmbH"


async def test_bookings_page_no_longer_shows_source_filter(client, admin_user):
    """Issue #184: "I do not need the field sources ... It does not
    matter if it is a paid invoice / manually added or added by CSV
    import." -- the source filter/column is gone from this page."""
    await web_login(client)
    await _enable_finances_module()
    account_id = await _make_account()

    response = await client.get(f"/finances/accounts/{account_id}/bookings")
    assert response.status_code == 200
    assert 'name="source"' not in response.text


async def test_bookings_pagination_json_endpoint(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    account_id = await _make_account()

    async with AsyncSessionLocal() as session:
        for i in range(60):
            session.add(AccountTransaction(
                account_id=account_id, booking_date=date.fromisoformat(f"2026-01-{(i % 28) + 1:02d}"), amount="-1.00",
                description=f"Entry {i}", source="manual",
            ))
        await session.commit()

    page1 = await client.get(f"/finances/accounts/{account_id}/bookings")
    assert page1.status_code == 200

    page2 = await client.get(f"/finances/accounts/{account_id}/bookings.json?offset=50")
    assert page2.status_code == 200
    data = page2.json()
    assert len(data["rows"]) == 10
    assert data["has_more"] is False


async def test_bookings_csv_export(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    account_id = await _make_account()

    async with AsyncSessionLocal() as session:
        session.add(AccountTransaction(
            account_id=account_id, booking_date=date.fromisoformat("2026-08-01"), amount="-9.99",
            description="Export me", counterparty="Export Counterparty Ltd", source="manual",
        ))
        await session.commit()

    response = await client.get(f"/finances/accounts/{account_id}/bookings/export.csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "Export me" in response.text
    assert "-9.99" in response.text
    assert "Export Counterparty Ltd" in response.text
    assert "Counterparty" in response.text.splitlines()[0]


async def _import_preview(client, account_id, csv_content):
    files = {"datei": ("import.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")}
    return await client.post(f"/finances/accounts/{account_id}/bookings/import/preview", files=files)


async def _import_confirm(client, account_id, csv_content, mapping, delimiter=";"):
    """mapping: dict of {column_index: target_field} -- mirrors what the
    preview step's mapping form would submit, without needing to parse
    the rendered HTML in every test."""
    data = {"csv_content_b64": base64.b64encode(csv_content.encode("utf-8")).decode("ascii"), "delimiter": delimiter}
    for index, field in mapping.items():
        data[f"map_{index}"] = field
    return await client.post(f"/finances/accounts/{account_id}/bookings/import/confirm", data=data)


async def test_bookings_csv_import_preview_guesses_mapping(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    account_id = await _make_account()

    csv_content = "Date;Amount;Description\n2026-08-01;-15.50;Supplies\n"
    response = await _import_preview(client, account_id, csv_content)
    assert response.status_code == 200
    assert "UndefinedError" not in response.text
    assert "Supplies" in response.text
    assert 'name="map_0"' in response.text


async def test_bookings_csv_import_confirm_creates_transactions(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    account_id = await _make_account()

    csv_content = "Date;Amount;Description\n2026-08-01;-15.50;Supplies\n2026-08-02;100.00;Membership refund\ninvalid;abc;Broken row\n"
    response = await _import_confirm(
        client, account_id, csv_content, {0: "date", 1: "amount", 2: "description"},
    )
    assert response.status_code in (302, 303)
    assert "imported=2" in response.headers["location"]
    assert "skipped=1" in response.headers["location"]

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AccountTransaction).where(AccountTransaction.account_id == account_id)
        )
        transactions = list(result.scalars().all())

    assert len(transactions) == 2
    assert all(t.source == "csv_import" for t in transactions)
    descriptions = {t.description for t in transactions}
    assert descriptions == {"Supplies", "Membership refund"}


async def test_bookings_csv_import_requires_date_and_amount_mapping(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    account_id = await _make_account()

    csv_content = "Date;Amount;Description\n2026-08-01;-15.50;Supplies\n"
    response = await _import_confirm(client, account_id, csv_content, {2: "description"})
    assert response.status_code in (302, 303)
    assert "error=" in response.headers["location"]

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AccountTransaction).where(AccountTransaction.account_id == account_id)
        )
        assert result.scalar_one_or_none() is None


async def test_bookings_csv_import_with_counterparty_column(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    account_id = await _make_account()

    csv_content = "Date;Amount;Description;Counterparty\n2026-08-01;-15.50;Supplies;Hardware Store\n"
    response = await _import_confirm(
        client, account_id, csv_content, {0: "date", 1: "amount", 2: "description", 3: "counterparty"},
    )
    assert response.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AccountTransaction).where(AccountTransaction.account_id == account_id)
        )
        transaction = result.scalar_one()
        assert transaction.counterparty == "Hardware Store"


async def test_bookings_csv_import_backfills_empty_member_iban(client, admin_user):
    """Issue #187: match by name against the mapped counterparty
    column, backfill IBAN only when the member's own IBAN is empty."""
    await web_login(client)
    await _enable_finances_module()
    account_id = await _make_account()

    async with AsyncSessionLocal() as session:
        member = Member(first_name="Erika", last_name="Musterfrau")
        session.add(member)
        await session.commit()
        member_id = member.id

    csv_content = (
        "Date;Amount;Description;Counterparty;IBAN\n"
        "2026-08-01;50.00;Dues;Erika Musterfrau;DE89370400440532013000\n"
    )
    response = await _import_confirm(
        client, account_id, csv_content,
        {0: "date", 1: "amount", 2: "description", 3: "counterparty", 4: "iban"},
    )
    assert response.status_code in (302, 303)
    assert "iban_updated=1" in response.headers["location"]

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Member).where(Member.id == member_id))
        refreshed = result.scalar_one()
        assert refreshed.iban == "DE89370400440532013000"


async def test_bookings_csv_import_does_not_overwrite_existing_member_iban(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    account_id = await _make_account()

    async with AsyncSessionLocal() as session:
        member = Member(first_name="Hans", last_name="Beispiel", iban="DE00000000000000000000")
        session.add(member)
        await session.commit()
        member_id = member.id

    csv_content = (
        "Date;Amount;Description;Counterparty;IBAN\n"
        "2026-08-01;50.00;Dues;Hans Beispiel;DE89370400440532013000\n"
    )
    response = await _import_confirm(
        client, account_id, csv_content,
        {0: "date", 1: "amount", 2: "description", 3: "counterparty", 4: "iban"},
    )
    assert response.status_code in (302, 303)
    assert "iban_updated=0" in response.headers["location"]

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Member).where(Member.id == member_id))
        refreshed = result.scalar_one()
        assert refreshed.iban == "DE00000000000000000000"


async def test_bookings_manual_add_and_delete(client, admin_user):
    await web_login(client)
    await _enable_finances_module()
    account_id = await _make_account()

    r_add = await client.post(f"/finances/accounts/{account_id}/bookings/manual", data={
        "booking_date": "2026-08-01", "amount": "-3.33", "description": "Coffee for the meeting",
    })
    assert r_add.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AccountTransaction).where(AccountTransaction.account_id == account_id)
        )
        transaction = result.scalar_one()
        assert transaction.source == "manual"
        assert float(transaction.amount) == -3.33

    r_delete = await client.post(f"/finances/accounts/{account_id}/bookings/{transaction.id}/delete")
    assert r_delete.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AccountTransaction).where(AccountTransaction.account_id == account_id)
        )
        assert result.scalar_one_or_none() is None


async def test_account_deletion_cascades_transactions(client, admin_user):
    """Unlike InvoicePayment.account_id (ON DELETE SET NULL, keeping the
    payment), an AccountTransaction only exists for its account -- it
    must be CASCADE-deleted with it."""
    await web_login(client)
    await _enable_finances_module()
    account_id = await _make_account()

    async with AsyncSessionLocal() as session:
        session.add(AccountTransaction(
            account_id=account_id, booking_date=date.fromisoformat("2026-08-01"), amount="-1.00",
            description="Should vanish with the account", source="manual",
        ))
        await session.commit()

    r_delete = await client.post(f"/finances/accounts/{account_id}/delete")
    assert r_delete.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AccountTransaction).where(AccountTransaction.account_id == account_id)
        )
        assert result.scalar_one_or_none() is None
