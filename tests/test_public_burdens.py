"""
Issue #163: new "Public burdens" ("öffentliche Lasten") pricing mode --
billed at one rate per sqm against the parcel's own leased area PLUS
its share of the communal area (Area B) lease, combining what PER_SQM
and COMMUNAL_AREA_SHARE each do separately. Reuses
COMMUNAL_AREA_SHARE's exact denominator/split logic (see
app/invoice_generation.py's communal_share_denominators), but --
confirmed with the reporter -- degrades gracefully when Area B isn't
configured: the parcel's own area is always billed, with the communal
share simply treated as 0 rather than skipping the item entirely
(unlike COMMUNAL_AREA_SHARE's all-or-nothing behavior).
"""
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import AsyncSessionLocal
from app.models import Member, MemberParcel, Parcel, Invoice, ClubSetting, InvoiceItemTemplate, InvoicePricingMode


async def _enable_finances_module():
    async with AsyncSessionLocal() as session:
        session.add(ClubSetting(key="modul_finances", value="true", description="test"))
        await session.commit()


async def _set_area_settings(total_sqm: str, area_c_sqm: str):
    async with AsyncSessionLocal() as session:
        session.add(ClubSetting(key="flaeche_gesamt_qm", value=total_sqm, description="test"))
        session.add(ClubSetting(key="flaeche_c_qm", value=area_c_sqm, description="test"))
        await session.commit()


async def _make_run(client, year="2026"):
    r_create = await client.post("/finances/runs", data={
        "year": year, "subject": "Public burdens test", "issued_date": f"{year}-08-01",
        "due_date": f"{year}-09-01", "footer_text": "",
    })
    assert r_create.status_code in (302, 303)
    return r_create.headers["location"].rstrip("/").split("/")[-1]


async def _occupied_parcel(session, plot_number: str, area_sqm: float) -> Parcel:
    parcel = Parcel(plot_number=plot_number, area_sqm=area_sqm)
    tenant = Member(
        first_name="Tenant", last_name=plot_number,
        street="Gartenweg 1", postal_code="12345", city="Testort",
    )
    session.add_all([parcel, tenant])
    await session.flush()
    session.add(MemberParcel(member_id=tenant.id, parcel_id=parcel.id, is_invoice_address=True))
    return parcel


async def test_public_burdens_bills_own_area_plus_evenly_split_communal_share(client, admin_user):
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    await _enable_finances_module()

    # Area A = 4 x 200 = 800. Total = 8800, Area C = 0. Area B = 8000.
    # Split across the 4 occupied parcels: 2000 each.
    # Each parcel's own area (200) + communal share (2000) = 2200 sqm,
    # billed at 0.04 EUR/sqm = 88.00.
    async with AsyncSessionLocal() as session:
        parcels = [await _occupied_parcel(session, f"PUBBURDEN-{i}", 200) for i in range(4)]
        await session.commit()

    await _set_area_settings("8800", "0")

    run_id = await _make_run(client)
    r_item = await client.post(f"/finances/runs/{run_id}/items", data={
        "order_number": "10", "name": "Public burdens", "description": "",
        "pricing_mode": "public_burdens", "unit_price": "0.04", "applies_to_all_parcels": "on",
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

    assert len(invoices) == 4
    for inv in invoices:
        line = inv.line_items[0]
        assert float(line.quantity) == 2200.0, "own area (200) + communal share (2000)"
        assert float(line.unit_price) == 0.04
        assert float(line.line_total) == 88.00


async def test_public_burdens_scoped_subset_uses_subset_as_communal_denominator(client, admin_user):
    """Same denominator behavior as COMMUNAL_AREA_SHARE: scoping to a
    subset changes the split to just that subset, still added on top
    of each scoped parcel's own area."""
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    await _enable_finances_module()

    # Area A = 4 x 250 = 1000. Total = 5000, Area C = 0. Area B = 4000.
    # Scoped to 2 of the 4 parcels: 4000 / 2 = 2000 each communal share.
    async with AsyncSessionLocal() as session:
        scoped = [await _occupied_parcel(session, f"PUBBURDEN-IN-{i}", 250) for i in range(2)]
        unscoped = [await _occupied_parcel(session, f"PUBBURDEN-OUT-{i}", 250) for i in range(2)]
        await session.commit()
        scoped_ids = [p.id for p in scoped]

    await _set_area_settings("5000", "0")

    run_id = await _make_run(client)
    r_item = await client.post(f"/finances/runs/{run_id}/items", data={
        "order_number": "10", "name": "Public burdens (scoped)", "description": "",
        "pricing_mode": "public_burdens", "unit_price": "1.00",
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
    assert billed_plot_numbers == {"PUBBURDEN-IN-0", "PUBBURDEN-IN-1"}
    for inv in invoices:
        line = inv.line_items[0]
        assert float(line.quantity) == 2250.0, "own area (250) + communal share (4000/2=2000)"


async def test_public_burdens_bills_own_area_only_when_area_b_not_configured(client, admin_user):
    """Confirmed behavioral difference from COMMUNAL_AREA_SHARE: with
    no Area B configured at all, the item still bills -- just the
    parcel's own area, communal share treated as 0."""
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    await _enable_finances_module()
    # Deliberately NOT calling _set_area_settings -- flaeche_gesamt_qm
    # defaults to 0, so compute_area_b_sqm comes out <= 0.

    async with AsyncSessionLocal() as session:
        parcel = await _occupied_parcel(session, "PUBBURDEN-NOAREAB", 300)
        await session.commit()

    run_id = await _make_run(client)
    r_item = await client.post(f"/finances/runs/{run_id}/items", data={
        "order_number": "10", "name": "Public burdens (no Area B)", "description": "",
        "pricing_mode": "public_burdens", "unit_price": "0.04", "applies_to_all_parcels": "on",
    })
    assert r_item.status_code in (302, 303)

    r_finalize = await client.post(f"/finances/runs/{run_id}/finalize")
    assert r_finalize.status_code in (302, 303), r_finalize.headers.get("location")

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Invoice)
            .options(selectinload(Invoice.line_items))
            .where(Invoice.invoice_run_id == run_id)
        )
        invoices = list(result.scalars().unique().all())

    assert len(invoices) == 1, "own-area charge must not silently disappear over missing Area B settings"
    line = invoices[0].line_items[0]
    assert float(line.quantity) == 300.0, "communal share treated as 0, own area still billed in full"
    assert float(line.line_total) == 12.00


async def test_public_burdens_item_template_create_round_trip(client, admin_user):
    """Regression shape of issue #160: a brand new enum value must
    round-trip through the item-templates endpoint (and the underlying
    Postgres enum column) without an invalid-enum-value crash."""
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    await _enable_finances_module()

    resp = await client.post(
        "/finances/item-templates",
        data={
            "order_number": "10", "name": "Public burdens template", "description": "",
            "pricing_mode": "public_burdens", "unit_price": "0.04", "applies_to_all_parcels": "on",
        },
    )
    assert resp.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(InvoiceItemTemplate).where(InvoiceItemTemplate.name == "Public burdens template")
        )
        template = result.scalar_one()

    assert template.pricing_mode == InvoicePricingMode.PUBLIC_BURDENS
    assert float(template.unit_price) == 0.04
