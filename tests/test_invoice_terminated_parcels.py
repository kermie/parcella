"""
Issue #166: parcels weren't billable at all once ParcelStatus flipped
to TERMINATED, even when a current invoice-address resident existed
(typically a new tenant who's taken over the plot before its status
gets flipped back to ACTIVE). app/invoice_generation.py's parcel query
was hard-filtered to ParcelStatus.ACTIVE only; fixed to exclude only
DELETED, matching the "!= DELETED" convention already used everywhere
else real parcels are counted (app/area_utils.py, app/routers/
api_stats.py, app/main.py's dashboard stats).

Confirmed with the reporter: this does NOT reopen ADR 0035's
constraint that a former tenant can never hold is_invoice_address --
a departed tenant is still never billed. Only the parcel-level status
filter changed.
"""
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import Member, MemberParcel, Parcel, ParcelStatus, Invoice, ClubSetting


async def _enable_finances_module():
    async with AsyncSessionLocal() as session:
        session.add(ClubSetting(key="modul_finances", value="true", description="test"))
        await session.commit()


async def _make_run(client, year="2026"):
    r_create = await client.post("/finances/runs", data={
        "year": year, "subject": "Terminated parcel test", "issued_date": f"{year}-08-01",
        "due_date": f"{year}-09-01", "footer_text": "",
    })
    assert r_create.status_code in (302, 303)
    return r_create.headers["location"].rstrip("/").split("/")[-1]


async def test_terminated_parcel_with_invoice_address_resident_is_billed(client, admin_user):
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    await _enable_finances_module()

    async with AsyncSessionLocal() as session:
        parcel = Parcel(plot_number="TERMINATED-1", area_sqm=200, status=ParcelStatus.TERMINATED)
        new_tenant = Member(
            first_name="New", last_name="Tenant",
            street="Gartenweg 1", postal_code="12345", city="Testort",
        )
        session.add_all([parcel, new_tenant])
        await session.flush()
        session.add(MemberParcel(member_id=new_tenant.id, parcel_id=parcel.id, is_invoice_address=True))
        await session.commit()
        parcel_id = parcel.id

    run_id = await _make_run(client)
    r_item = await client.post(f"/finances/runs/{run_id}/items", data={
        "order_number": "10", "name": "Membership fee", "description": "",
        "pricing_mode": "fixed_per_parcel", "unit_price": "50.00", "applies_to_all_parcels": "on",
    })
    assert r_item.status_code in (302, 303)

    r_finalize = await client.post(f"/finances/runs/{run_id}/finalize")
    assert r_finalize.status_code in (302, 303), r_finalize.headers.get("location")

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Invoice).where(Invoice.invoice_run_id == run_id))
        invoices = result.scalars().all()

    assert len(invoices) == 1, "a TERMINATED parcel with a current invoice-address resident must still be billed"
    assert invoices[0].parcel_id == parcel_id
    assert float(invoices[0].subtotal) == 50.0


async def test_deleted_parcel_is_never_billed(client, admin_user):
    """DELETED is a genuine soft-delete (app/routers/api_parcels.py sets
    it), unlike TERMINATED -- must stay excluded."""
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    await _enable_finances_module()

    async with AsyncSessionLocal() as session:
        parcel = Parcel(plot_number="DELETED-1", area_sqm=200, status=ParcelStatus.DELETED)
        tenant = Member(
            first_name="Ghost", last_name="Tenant",
            street="Gartenweg 1", postal_code="12345", city="Testort",
        )
        session.add_all([parcel, tenant])
        await session.flush()
        session.add(MemberParcel(member_id=tenant.id, parcel_id=parcel.id, is_invoice_address=True))
        await session.commit()

    run_id = await _make_run(client)
    r_item = await client.post(f"/finances/runs/{run_id}/items", data={
        "order_number": "10", "name": "Membership fee", "description": "",
        "pricing_mode": "fixed_per_parcel", "unit_price": "50.00", "applies_to_all_parcels": "on",
    })
    assert r_item.status_code in (302, 303)

    r_finalize = await client.post(f"/finances/runs/{run_id}/finalize")
    assert r_finalize.status_code in (302, 303), r_finalize.headers.get("location")

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Invoice).where(Invoice.invoice_run_id == run_id))
        invoices = result.scalars().all()

    assert len(invoices) == 0, "a DELETED (soft-deleted) parcel must never be billed"


async def test_terminated_parcel_with_only_former_tenant_is_not_billed(client, admin_user):
    """ADR 0035 is unchanged: a former tenant (assigned_until set) can
    never hold is_invoice_address, so a TERMINATED parcel with no
    *current* invoice-address resident is correctly skipped, not billed
    to whoever moved out."""
    from datetime import date

    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    await _enable_finances_module()

    async with AsyncSessionLocal() as session:
        parcel = Parcel(plot_number="TERMINATED-2", area_sqm=200, status=ParcelStatus.TERMINATED)
        former_tenant = Member(
            first_name="Former", last_name="Tenant",
            street="Gartenweg 1", postal_code="12345", city="Testort",
        )
        session.add_all([parcel, former_tenant])
        await session.flush()
        session.add(MemberParcel(
            member_id=former_tenant.id, parcel_id=parcel.id,
            is_invoice_address=False, assigned_until=date(2026, 1, 1),
        ))
        await session.commit()

    run_id = await _make_run(client)
    r_item = await client.post(f"/finances/runs/{run_id}/items", data={
        "order_number": "10", "name": "Membership fee", "description": "",
        "pricing_mode": "fixed_per_parcel", "unit_price": "50.00", "applies_to_all_parcels": "on",
    })
    assert r_item.status_code in (302, 303)

    r_finalize = await client.post(f"/finances/runs/{run_id}/finalize")
    assert r_finalize.status_code in (302, 303), r_finalize.headers.get("location")

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Invoice).where(Invoice.invoice_run_id == run_id))
        invoices = result.scalars().all()

    assert len(invoices) == 0, "a vacant terminated parcel with no current invoice-address resident must not be billed"
