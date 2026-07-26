"""
Issue #82: a new "share of the communal area lease" pricing mode.
Area B (communal, see app/area_utils.py) is split evenly across
however many parcels actually get billed for the item -- the club
still enters the price per sqm by hand. Covers: the split is even
across all billable parcels, a vacant parcel doesn't count toward the
split (and isn't billed itself), and scoping the item to a subset of
parcels changes the denominator to just that subset.
"""
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import AsyncSessionLocal
from app.models import Member, MemberParcel, Parcel, Invoice, InvoiceLineItem, ClubSetting


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
        "year": year, "subject": "Communal share test", "issued_date": f"{year}-08-01",
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


async def test_communal_area_share_splits_area_b_evenly_and_skips_vacant_parcels(client, admin_user):
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    await _enable_finances_module()

    # Area A counts EVERY parcel regardless of lease status (issues
    # #80/#81), so all 5 parcels (4 occupied + 1 vacant) contribute:
    # Area A = 5 x 200 = 1000. Total = 9000, Area C = 0.
    # Area B = 9000 - 1000 - 0 = 8000. But the SPLIT only counts the 4
    # OCCUPIED parcels (the vacant one doesn't get billed at all):
    # 8000 / 4 = 2000 each.
    async with AsyncSessionLocal() as session:
        billed_parcels = [await _occupied_parcel(session, f"COMMSHARE-{i}", 200) for i in range(4)]
        vacant_parcel = Parcel(plot_number="COMMSHARE-VACANT", area_sqm=200)
        session.add(vacant_parcel)
        await session.commit()
        billed_ids = {p.id for p in billed_parcels}

    await _set_area_settings("9000", "0")

    run_id = await _make_run(client)
    r_item = await client.post(f"/finances/runs/{run_id}/items", data={
        "order_number": "10", "name": "Communal area share", "description": "",
        "pricing_mode": "communal_area_share", "unit_price": "2.00", "applies_to_all_parcels": "on",
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
    assert billed_plot_numbers == {"COMMSHARE-0", "COMMSHARE-1", "COMMSHARE-2", "COMMSHARE-3"}, (
        "the vacant parcel must not be billed at all"
    )
    for inv in invoices:
        assert inv.parcel_id in billed_ids
        assert len(inv.line_items) == 1
        line = inv.line_items[0]
        assert float(line.quantity) == 2000.0, "Area B (8000) split across the 4 billable parcels is 2000 each"
        assert float(line.unit_price) == 2.0
        assert float(line.line_total) == 4000.0


async def test_communal_area_share_scoped_to_subset_uses_subset_as_denominator(client, admin_user):
    """Scoping the item to specific parcels changes the split to just
    that subset, not every parcel in the club."""
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    await _enable_finances_module()

    # Area A = 4 x 250 = 1000. Total = 5000, Area C = 0. Area B = 4000.
    # Scoped to 2 of the 4 parcels: 4000 / 2 = 2000 each; the other 2
    # parcels are occupied but out of scope, so they're excluded both
    # from the split AND from being billed for this item.
    async with AsyncSessionLocal() as session:
        scoped = [await _occupied_parcel(session, f"COMMSCOPE-IN-{i}", 250) for i in range(2)]
        unscoped = [await _occupied_parcel(session, f"COMMSCOPE-OUT-{i}", 250) for i in range(2)]
        await session.commit()
        scoped_ids = [p.id for p in scoped]

    await _set_area_settings("5000", "0")

    run_id = await _make_run(client)
    r_item = await client.post(f"/finances/runs/{run_id}/items", data={
        "order_number": "10", "name": "Communal area share (scoped)", "description": "",
        "pricing_mode": "communal_area_share", "unit_price": "1.50",
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
    assert billed_plot_numbers == {"COMMSCOPE-IN-0", "COMMSCOPE-IN-1"}, (
        "only the scoped parcels get billed for this item"
    )
    for inv in invoices:
        line = inv.line_items[0]
        assert float(line.quantity) == 2000.0, "Area B (4000) split across the 2 SCOPED parcels is 2000 each"
        assert float(line.line_total) == 3000.0
