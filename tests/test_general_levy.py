"""
Issue #171: a new "General levy" ("Umlage") pricing mode -- unit_price
holds a single TOTAL amount the club needs to cover (e.g. a one-off
legal fee), split evenly across every billable parcel. Reuses the
exact same billable-parcel denominator as COMMUNAL_AREA_SHARE/
PUBLIC_BURDENS (app/invoice_generation.py's billable_parcel_denominators):
confirmed with the reporter that a vacant parcel must never count
toward the split, so the entered total is always collected in full.
"""
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import AsyncSessionLocal
from app.models import Member, MemberParcel, Parcel, Invoice, InvoiceItemTemplate, InvoicePricingMode


async def _enable_finances_module():
    from app.models import ClubSetting
    async with AsyncSessionLocal() as session:
        session.add(ClubSetting(key="modul_finances", value="true", description="test"))
        await session.commit()


async def _make_run(client, year="2026"):
    r_create = await client.post("/finances/runs", data={
        "year": year, "subject": "General levy test", "issued_date": f"{year}-08-01",
        "due_date": f"{year}-09-01", "footer_text": "",
    })
    assert r_create.status_code in (302, 303)
    return r_create.headers["location"].rstrip("/").split("/")[-1]


async def _occupied_parcel(session, plot_number: str) -> Parcel:
    parcel = Parcel(plot_number=plot_number)
    tenant = Member(
        first_name="Tenant", last_name=plot_number,
        street="Gartenweg 1", postal_code="12345", city="Testort",
    )
    session.add_all([parcel, tenant])
    await session.flush()
    session.add(MemberParcel(member_id=tenant.id, parcel_id=parcel.id, is_invoice_address=True))
    return parcel


async def test_general_levy_splits_total_evenly_across_billable_parcels(client, admin_user):
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    await _enable_finances_module()

    async with AsyncSessionLocal() as session:
        billed_parcels = [await _occupied_parcel(session, f"LEVY-{i}") for i in range(4)]
        vacant_parcel = Parcel(plot_number="LEVY-VACANT")
        session.add(vacant_parcel)
        await session.commit()
        billed_ids = {p.id for p in billed_parcels}

    run_id = await _make_run(client)
    r_item = await client.post(f"/finances/runs/{run_id}/items", data={
        "order_number": "10", "name": "Legal fees 2026", "description": "",
        "pricing_mode": "general_levy", "unit_price": "1000.00", "applies_to_all_parcels": "on",
    })
    assert r_item.status_code in (302, 303)

    r_finalize = await client.post(f"/finances/runs/{run_id}/finalize")
    assert r_finalize.status_code in (302, 303), r_finalize.headers.get("location")

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Invoice)
            .options(selectinload(Invoice.parcel), selectinload(Invoice.line_items))
            .where(Invoice.invoice_run_id == run_id)
        )
        invoices = list(result.scalars().unique().all())

    billed_plot_numbers = {inv.parcel.plot_number for inv in invoices}
    assert billed_plot_numbers == {"LEVY-0", "LEVY-1", "LEVY-2", "LEVY-3"}, (
        "the vacant parcel must not be billed at all, and must not count toward the split"
    )
    for inv in invoices:
        assert inv.parcel_id in billed_ids
        assert len(inv.line_items) == 1
        line = inv.line_items[0]
        assert float(line.quantity) == 1.0
        assert float(line.unit_price) == 250.0, "1000.00 split evenly across the 4 billable parcels is 250.00 each"
        assert float(line.line_total) == 250.0


async def test_general_levy_scoped_to_subset_uses_subset_as_denominator(client, admin_user):
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    await _enable_finances_module()

    async with AsyncSessionLocal() as session:
        scoped = [await _occupied_parcel(session, f"LEVYSCOPE-IN-{i}") for i in range(2)]
        unscoped = [await _occupied_parcel(session, f"LEVYSCOPE-OUT-{i}") for i in range(2)]
        await session.commit()
        scoped_ids = [p.id for p in scoped]

    run_id = await _make_run(client)
    r_item = await client.post(f"/finances/runs/{run_id}/items", data={
        "order_number": "10", "name": "Legal fees (scoped)", "description": "",
        "pricing_mode": "general_levy", "unit_price": "300.00",
        "parcel_ids": scoped_ids,
    })
    assert r_item.status_code in (302, 303)

    r_finalize = await client.post(f"/finances/runs/{run_id}/finalize")
    assert r_finalize.status_code in (302, 303), r_finalize.headers.get("location")

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Invoice)
            .options(selectinload(Invoice.parcel), selectinload(Invoice.line_items))
            .where(Invoice.invoice_run_id == run_id)
        )
        invoices = list(result.scalars().unique().all())

    billed_plot_numbers = {inv.parcel.plot_number for inv in invoices}
    assert billed_plot_numbers == {"LEVYSCOPE-IN-0", "LEVYSCOPE-IN-1"}, "only the scoped parcels get billed"
    for inv in invoices:
        line = inv.line_items[0]
        assert float(line.unit_price) == 150.0, "300.00 split across the 2 SCOPED parcels is 150.00 each"


async def test_general_levy_bills_nothing_when_no_billable_parcels(client, admin_user):
    """A zero denominator must not crash (divide-by-zero) -- the item
    simply bills nothing, same as COMMUNAL_AREA_SHARE's behavior."""
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    await _enable_finances_module()

    async with AsyncSessionLocal() as session:
        session.add(Parcel(plot_number="LEVY-EMPTY-VACANT"))
        await session.commit()

    run_id = await _make_run(client)
    r_item = await client.post(f"/finances/runs/{run_id}/items", data={
        "order_number": "10", "name": "Legal fees (no residents)", "description": "",
        "pricing_mode": "general_levy", "unit_price": "500.00", "applies_to_all_parcels": "on",
    })
    assert r_item.status_code in (302, 303)

    r_finalize = await client.post(f"/finances/runs/{run_id}/finalize")
    assert r_finalize.status_code in (302, 303), r_finalize.headers.get("location")

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Invoice).where(Invoice.invoice_run_id == run_id))
        invoices = result.scalars().all()

    assert len(invoices) == 0


async def test_general_levy_rounds_to_cents(client, admin_user):
    """1000.00 / 3 = 333.333... -- must round to cents (0.01), not the
    tenth-of-a-sqm precision the area-based modes use."""
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    await _enable_finances_module()

    async with AsyncSessionLocal() as session:
        parcels = [await _occupied_parcel(session, f"LEVYROUND-{i}") for i in range(3)]
        await session.commit()

    run_id = await _make_run(client)
    r_item = await client.post(f"/finances/runs/{run_id}/items", data={
        "order_number": "10", "name": "Legal fees (rounding)", "description": "",
        "pricing_mode": "general_levy", "unit_price": "1000.00", "applies_to_all_parcels": "on",
    })
    assert r_item.status_code in (302, 303)

    r_finalize = await client.post(f"/finances/runs/{run_id}/finalize")
    assert r_finalize.status_code in (302, 303), r_finalize.headers.get("location")

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Invoice).options(selectinload(Invoice.line_items)).where(Invoice.invoice_run_id == run_id)
        )
        invoices = list(result.scalars().unique().all())

    assert len(invoices) == 3
    for inv in invoices:
        line = inv.line_items[0]
        assert float(line.unit_price) == 333.33


async def test_general_levy_item_template_create_round_trip(client, admin_user):
    """Regression shape of issue #160: a brand new enum value must
    round-trip through the item-templates endpoint (and the underlying
    Postgres enum column) without an invalid-enum-value crash."""
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    await _enable_finances_module()

    resp = await client.post(
        "/finances/item-templates",
        data={
            "order_number": "10", "name": "General levy template", "description": "",
            "pricing_mode": "general_levy", "unit_price": "1000.00", "applies_to_all_parcels": "on",
        },
    )
    assert resp.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(InvoiceItemTemplate).where(InvoiceItemTemplate.name == "General levy template")
        )
        template = result.scalar_one()

    assert template.pricing_mode == InvoicePricingMode.GENERAL_LEVY
    assert float(template.unit_price) == 1000.00
