"""
Issues #87/#89 follow-up: the "insurance cost" invoice pricing mode
bills whichever parcels app/insurance_utils.py's calculate_insurance_cost()
computes a nonzero total for -- property + accident combined, including
the "+1" per-additional-person accident surcharge -- with no manual
parcel scoping, exactly mirroring how WORK_HOURS_SHORTFALL (issue #83)
is fully automatic. A parcel_scopes/applies_to_all_parcels selection is
still stored on the item (the form doesn't special-case what it
submits) but must be ignored for eligibility: an insured parcel left
OUT of the scope must still be billed, and an uninsured parcel must
never be billed regardless of being IN scope.
"""
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import AsyncSessionLocal
from app.models import (
    Member, MemberParcel, Parcel, Invoice, ClubSetting,
    PropertyInsurancePackage, InsuranceConfiguration, ParcelInsurance,
    AccidentInsuranceAdditionalPerson,
)


async def _enable_modules():
    async with AsyncSessionLocal() as session:
        session.add(ClubSetting(key="modul_finances", value="true", description="test"))
        session.add(ClubSetting(key="modul_insurance", value="true", description="test"))
        await session.commit()


async def _make_run(client, year="2026"):
    r_create = await client.post("/finances/runs", data={
        "year": year, "subject": "Insurance cost test", "issued_date": f"{year}-08-01",
        "due_date": f"{year}-09-01", "footer_text": "",
    })
    assert r_create.status_code in (302, 303)
    return r_create.headers["location"].rstrip("/").split("/")[-1]


async def _occupied_parcel(session, plot_number: str) -> tuple:
    parcel = Parcel(plot_number=plot_number, area_sqm=200)
    tenant = Member(
        first_name="Tenant", last_name=plot_number,
        street=f"{plot_number} Street", postal_code="12345", city="Testort",
    )
    extra = Member(
        first_name="Extra", last_name=plot_number,
        street="Elsewhere 1", postal_code="54321", city="Otherville",
    )
    session.add_all([parcel, tenant, extra])
    await session.flush()
    session.add(MemberParcel(member_id=tenant.id, parcel_id=parcel.id, is_invoice_address=True))
    return parcel, tenant, extra


async def test_insurance_cost_ignores_parcel_scope_and_skips_uninsured_parcels(client, admin_user):
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    await _enable_modules()

    year = 2026
    async with AsyncSessionLocal() as session:
        package = PropertyInsurancePackage(year=year, name="Package 1", amount_eur=60)
        config = InsuranceConfiguration(year=year, accident_base_amount_eur=20, accident_additional_amount_eur=10)
        session.add_all([package, config])
        await session.flush()

        insured_parcel, insured_tenant, additional_person = await _occupied_parcel(session, "INSCOST-INSURED")
        uninsured_parcel, _, _ = await _occupied_parcel(session, "INSCOST-BARE")
        await session.flush()

        pi = ParcelInsurance(
            parcel_id=insured_parcel.id, year=year,
            has_property_insurance=True, property_package_id=package.id,
            has_accident_insurance=True,
        )
        session.add(pi)
        await session.flush()
        session.add(AccidentInsuranceAdditionalPerson(parcel_insurance_id=pi.id, member_id=additional_person.id))
        await session.commit()

        insured_id, bare_id = insured_parcel.id, uninsured_parcel.id

    run_id = await _make_run(client, str(year))
    # Deliberately scope the item to ONLY the uninsured parcel -- if
    # scoping were honored, the insured parcel would never be billed;
    # if it's correctly ignored, the insured parcel is billed anyway
    # and the (scoped-in) uninsured parcel still isn't, since it has
    # no insurance cost to charge.
    r_item = await client.post(f"/finances/runs/{run_id}/items", data={
        "order_number": "10", "name": "Insurance cost", "description": "",
        "pricing_mode": "insurance_cost", "parcel_ids": [bare_id],
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
    assert billed_plot_numbers == {"INSCOST-INSURED"}, (
        "the insured parcel must be billed even though it was left OUT of parcel_scopes, "
        "and the uninsured parcel must not be billed even though it was left IN parcel_scopes"
    )
    line = invoices[0].line_items[0]
    # property (60) + accident base (20) + one additional person (10) = 90
    assert float(line.line_total) == 90.0
