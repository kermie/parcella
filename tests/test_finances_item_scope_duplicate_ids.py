"""
Bug report: "adding a new item to /finances/item-templates gives an
internal server error." Root cause found in the real production DB's
logs: item_template_create/item_template_update (and the analogous
run-level item_create/item_update) insert one InvoiceItemTemplateParcel/
InvoiceItemDefinitionParcel (or ...Member) row per submitted id without
deduplicating first -- a resubmitted form (double form-resubmission on
browser back/forward, a retried request) whose parcel_ids/member_ids
list contains the same id twice crashes the INSERT with a
UniqueViolationError on the (definition_id, parcel_id) constraint,
surfaced to the browser as a 500.

Fixed via app/routers/finances.py's _dedupe_ids(), applied at all 4
call sites (item_template_create, item_template_update, item_create,
item_update). These tests submit an intentionally-duplicated id list
(the reported bug's exact shape) and assert a clean redirect plus
exactly one scope row persisted, not two.
"""
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import (
    ClubSetting, Parcel, InvoiceItemTemplate, InvoiceItemTemplateParcel,
    InvoiceItemDefinition, InvoiceItemDefinitionParcel,
)


async def _enable_finances_module():
    async with AsyncSessionLocal() as session:
        session.add(ClubSetting(key="modul_finances", value="true", description="test"))
        await session.commit()


async def _create_active_parcel(plot_number: str) -> str:
    async with AsyncSessionLocal() as session:
        parcel = Parcel(plot_number=plot_number, area_sqm=100)
        session.add(parcel)
        await session.commit()
        await session.refresh(parcel)
        return parcel.id


async def _make_run(client, year="2026"):
    r_create = await client.post("/finances/runs", data={
        "year": year, "subject": "Dedup test", "issued_date": f"{year}-08-01",
        "due_date": f"{year}-09-01", "footer_text": "",
    })
    assert r_create.status_code in (302, 303)
    return r_create.headers["location"].rstrip("/").split("/")[-1]


async def test_item_template_create_with_duplicate_parcel_ids_does_not_500(client, admin_user):
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    await _enable_finances_module()
    parcel_id = await _create_active_parcel("DEDUP-1")

    resp = await client.post(
        "/finances/item-templates",
        data={
            "order_number": "10", "name": "Dedup template", "description": "",
            "pricing_mode": "fixed_per_parcel", "unit_price": "9.00",
            "parcel_ids": [parcel_id, parcel_id],
        },
    )
    assert resp.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(InvoiceItemTemplate).where(InvoiceItemTemplate.name == "Dedup template"))
        template = result.scalar_one()
        scopes_result = await session.execute(
            select(InvoiceItemTemplateParcel).where(InvoiceItemTemplateParcel.invoice_item_template_id == template.id)
        )
        assert len(scopes_result.scalars().all()) == 1


async def test_item_template_update_with_duplicate_parcel_ids_does_not_500(client, admin_user):
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    await _enable_finances_module()
    parcel_id = await _create_active_parcel("DEDUP-2")

    r_create = await client.post(
        "/finances/item-templates",
        data={
            "order_number": "10", "name": "Dedup template edit", "description": "",
            "pricing_mode": "fixed_per_parcel", "unit_price": "9.00",
        },
    )
    assert r_create.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(InvoiceItemTemplate).where(InvoiceItemTemplate.name == "Dedup template edit")
        )
        template = result.scalar_one()

    resp = await client.post(
        f"/finances/item-templates/{template.id}/edit",
        data={
            "order_number": "10", "name": "Dedup template edit", "description": "",
            "pricing_mode": "fixed_per_parcel", "unit_price": "9.00",
            "parcel_ids": [parcel_id, parcel_id],
        },
    )
    assert resp.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
        scopes_result = await session.execute(
            select(InvoiceItemTemplateParcel).where(InvoiceItemTemplateParcel.invoice_item_template_id == template.id)
        )
        assert len(scopes_result.scalars().all()) == 1


async def test_run_item_create_with_duplicate_parcel_ids_does_not_500(client, admin_user):
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    await _enable_finances_module()
    parcel_id = await _create_active_parcel("DEDUP-3")
    run_id = await _make_run(client)

    resp = await client.post(
        f"/finances/runs/{run_id}/items",
        data={
            "order_number": "10", "name": "Dedup run item", "description": "",
            "pricing_mode": "fixed_per_parcel", "unit_price": "9.00",
            "parcel_ids": [parcel_id, parcel_id],
        },
    )
    assert resp.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(InvoiceItemDefinition).where(InvoiceItemDefinition.name == "Dedup run item")
        )
        item = result.scalar_one()
        scopes_result = await session.execute(
            select(InvoiceItemDefinitionParcel).where(InvoiceItemDefinitionParcel.invoice_item_definition_id == item.id)
        )
        assert len(scopes_result.scalars().all()) == 1


async def test_run_item_update_with_duplicate_parcel_ids_does_not_500(client, admin_user):
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    await _enable_finances_module()
    parcel_id = await _create_active_parcel("DEDUP-4")
    run_id = await _make_run(client)

    r_create = await client.post(
        f"/finances/runs/{run_id}/items",
        data={
            "order_number": "10", "name": "Dedup run item edit", "description": "",
            "pricing_mode": "fixed_per_parcel", "unit_price": "9.00",
        },
    )
    assert r_create.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(InvoiceItemDefinition).where(InvoiceItemDefinition.name == "Dedup run item edit")
        )
        item = result.scalar_one()

    resp = await client.post(
        f"/finances/runs/{run_id}/items/{item.id}/edit",
        data={
            "order_number": "10", "name": "Dedup run item edit", "description": "",
            "pricing_mode": "fixed_per_parcel", "unit_price": "9.00",
            "parcel_ids": [parcel_id, parcel_id],
        },
    )
    assert resp.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
        scopes_result = await session.execute(
            select(InvoiceItemDefinitionParcel).where(InvoiceItemDefinitionParcel.invoice_item_definition_id == item.id)
        )
        assert len(scopes_result.scalars().all()) == 1
