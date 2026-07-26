"""
Issue #83: a new "work-hours shortfall" invoice pricing mode that
charges whoever /work-hours/evaluation currently shows owing money for
the year -- automatically excluding anyone exempt or who already
fulfilled their hours -- with no manual parcel/member scoping at all
(a member explicitly asked for "fully automatic, no picker"). Follows
whichever mode (PER_PARCEL/PER_MEMBER) that year's WorkHoursConfiguration
is set to, exactly matching /work-hours/evaluation for that same year.
"""
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import AsyncSessionLocal
from app.models import (
    Member, MemberParcel, Parcel, Invoice, ClubSetting,
    WorkHoursConfiguration, WorkHoursMode, ClubRole, MemberClubRole, ExemptionReason,
    WorkSession, SessionParticipation, SessionType, ParticipationStatus,
)


async def _enable_finances_module():
    async with AsyncSessionLocal() as session:
        session.add(ClubSetting(key="modul_finances", value="true", description="test"))
        await session.commit()


async def _make_run(client, year):
    r_create = await client.post("/finances/runs", data={
        "year": str(year), "subject": "Work-hours shortfall test", "issued_date": f"{year}-08-01",
        "due_date": f"{year}-09-01", "footer_text": "",
    })
    assert r_create.status_code in (302, 303)
    return r_create.headers["location"].rstrip("/").split("/")[-1]


async def _credit_hours(session, member_id: str, year: int, hours: float):
    if hours <= 0:
        return
    work_session = WorkSession(title="Session", type=SessionType.STANDARD, date=date(year, 4, 1))
    session.add(work_session)
    await session.flush()
    session.add(SessionParticipation(
        session_id=work_session.id, member_id=member_id,
        status=ParticipationStatus.ATTENDED, hours_completed=hours,
    ))


async def _exempt_member(session, member_id: str, year: int):
    role = ClubRole(name=f"Exempt-{member_id}", hours_exempt=True, exemption_reason=ExemptionReason.BOARD)
    session.add(role)
    await session.flush()
    session.add(MemberClubRole(member_id=member_id, club_role_id=role.id, year=year))


async def test_work_hours_shortfall_per_parcel_bills_shortfall_and_ignores_manual_scoping(client, admin_user):
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    await _enable_finances_module()
    year = 2026

    async with AsyncSessionLocal() as session:
        session.add(WorkHoursConfiguration(
            year=year, hours_required=5.0, rate_per_hour_eur=25.00, mode=WorkHoursMode.PER_PARCEL,
        ))

        owing_member = Member(
            first_name="Owing", last_name="Tenant",
            street="Gartenweg 1", postal_code="12345", city="Testort",
        )
        fulfilled_member = Member(
            first_name="Fulfilled", last_name="Tenant",
            street="Gartenweg 2", postal_code="12345", city="Testort",
        )
        exempt_member = Member(
            first_name="Exempt", last_name="Tenant",
            street="Gartenweg 3", postal_code="12345", city="Testort",
        )
        owing_parcel = Parcel(plot_number="WHSHORT-OWING", area_sqm=100)
        fulfilled_parcel = Parcel(plot_number="WHSHORT-FULFILLED", area_sqm=100)
        exempt_parcel = Parcel(plot_number="WHSHORT-EXEMPT", area_sqm=100)
        session.add_all([
            owing_member, fulfilled_member, exempt_member, owing_parcel, fulfilled_parcel, exempt_parcel,
        ])
        await session.flush()

        session.add(MemberParcel(member_id=owing_member.id, parcel_id=owing_parcel.id, is_invoice_address=True))
        session.add(MemberParcel(member_id=fulfilled_member.id, parcel_id=fulfilled_parcel.id, is_invoice_address=True))
        session.add(MemberParcel(member_id=exempt_member.id, parcel_id=exempt_parcel.id, is_invoice_address=True))

        await _credit_hours(session, owing_member.id, year, 2.0)       # outstanding 3.0 -> 75.00
        await _credit_hours(session, fulfilled_member.id, year, 5.0)   # fulfilled -> 0.00, not billed
        # exempt_member: 0 hours credited, but exempt -> must not be billed regardless
        await _exempt_member(session, exempt_member.id, year)

        await session.commit()

    run_id = await _make_run(client, year)
    # Deliberately submit applies_to_all_parcels OFF with no parcel_ids --
    # if manual scoping had any effect here, this would exclude every
    # parcel. It must not: the owing parcel still gets billed.
    r_item = await client.post(f"/finances/runs/{run_id}/items", data={
        "order_number": "10", "name": "Work-hours shortfall", "description": "",
        "pricing_mode": "work_hours_shortfall", "unit_price": "999.00",
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
    assert billed_plot_numbers == {"WHSHORT-OWING"}, (
        "only the parcel with an actual shortfall gets billed; fulfilled and exempt parcels must not"
    )
    line = invoices[0].line_items[0]
    assert float(line.quantity) == 1.0
    assert float(line.unit_price) == 75.00, "computed automatically (5-2 outstanding hours x 25.00/h), not the typed 999.00"
    assert float(line.line_total) == 75.00


async def test_work_hours_shortfall_per_member_bills_shortfall_and_ignores_manual_scoping(client, admin_user):
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    await _enable_finances_module()
    year = 2027

    async with AsyncSessionLocal() as session:
        session.add(WorkHoursConfiguration(
            year=year, hours_required=4.0, rate_per_hour_eur=10.00, mode=WorkHoursMode.PER_MEMBER,
        ))

        owing_member = Member(
            first_name="OwingM", last_name="Member",
            street="Vereinsstr 1", postal_code="12345", city="Testort",
        )
        fulfilled_member = Member(
            first_name="FulfilledM", last_name="Member",
            street="Vereinsstr 2", postal_code="12345", city="Testort",
        )
        exempt_member = Member(
            first_name="ExemptM", last_name="Member",
            street="Vereinsstr 3", postal_code="12345", city="Testort",
        )
        parcel = Parcel(plot_number="WHSHORT-PARCEL", area_sqm=300)
        session.add_all([owing_member, fulfilled_member, exempt_member, parcel])
        await session.flush()

        # PER_MEMBER evaluation only considers members WITH a parcel
        # assignment (Member.parcel_assignments.any()) -- is_invoice_address
        # is irrelevant here since member invoices don't route through it.
        session.add(MemberParcel(member_id=owing_member.id, parcel_id=parcel.id, is_invoice_address=False))
        session.add(MemberParcel(member_id=fulfilled_member.id, parcel_id=parcel.id, is_invoice_address=False))
        session.add(MemberParcel(member_id=exempt_member.id, parcel_id=parcel.id, is_invoice_address=False))

        await _credit_hours(session, owing_member.id, year, 1.0)       # outstanding 3.0 -> 30.00
        await _credit_hours(session, fulfilled_member.id, year, 4.0)   # fulfilled -> 0.00, not billed
        await _exempt_member(session, exempt_member.id, year)

        await session.commit()

    run_id = await _make_run(client, year)
    r_item = await client.post(f"/finances/runs/{run_id}/items", data={
        "order_number": "10", "name": "Work-hours shortfall", "description": "",
        "pricing_mode": "work_hours_shortfall", "unit_price": "999.00",
    })
    assert r_item.status_code in (302, 303)

    r_finalize = await client.post(f"/finances/runs/{run_id}/finalize")
    assert r_finalize.status_code in (302, 303), r_finalize.headers.get("location")

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Invoice)
            .options(selectinload(Invoice.member), selectinload(Invoice.line_items))
            .where(Invoice.invoice_run_id == run_id)
        )
        invoices = list(result.scalars().unique().all())

    billed_names = {inv.member.full_name for inv in invoices}
    assert billed_names == {"OwingM Member"}, (
        "only the member with an actual shortfall gets billed; fulfilled and exempt members must not"
    )
    line = invoices[0].line_items[0]
    assert float(line.quantity) == 1.0
    assert float(line.unit_price) == 30.00, "computed automatically (4-1 outstanding hours x 10.00/h), not the typed 999.00"
    assert float(line.line_total) == 30.00
