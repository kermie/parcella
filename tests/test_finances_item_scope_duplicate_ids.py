"""
Bug report: "adding a new item to /finances/item-templates gives an
internal server error." First pass fixed a duplicate-id-within-one-
submission case (_dedupe_ids()), but the report persisted after that
fix shipped -- the real production DB showed the exact same crash
still happening on an *edit* of a template that already had an
existing, UNCHANGED parcel scope.

Root cause (confirmed against the real DB): item_template_update/
item_update delete the old scope rows and re-add rows built from the
submitted ids in the same flush, without an explicit flush() in
between. SQLAlchemy's unit of work flushes INSERTs before DELETEs for
a given table within one flush -- so re-selecting a parcel/member that
was ALREADY scoped (no client-side duplication needed at all) tries to
INSERT the new row while the identical old row is still physically
present, tripping the (definition_id, parcel_id)/(definition_id,
member_id) unique constraint. Fixed by flushing right after each
delete loop, before the corresponding add loop, in both update routes.

These tests cover both shapes: a duplicated id within one submission
(first-pass fix, _dedupe_ids()), and resaving a template/item with an
unchanged, already-scoped parcel (second-pass fix, the flush()).
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


async def test_item_template_update_with_unchanged_scope_does_not_500(client, admin_user):
    """The actual reported scenario: resaving a template whose scope
    already includes a parcel, submitting that SAME parcel again (no
    duplication within the request itself) -- must not crash on the
    delete-then-reinsert of an unchanged scope row."""
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    await _enable_finances_module()
    parcel_id = await _create_active_parcel("DEDUP-5")

    r_create = await client.post(
        "/finances/item-templates",
        data={
            "order_number": "10", "name": "Unchanged scope template", "description": "",
            "pricing_mode": "fixed_per_parcel", "unit_price": "9.00",
            "parcel_ids": [parcel_id],
        },
    )
    assert r_create.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(InvoiceItemTemplate).where(InvoiceItemTemplate.name == "Unchanged scope template")
        )
        template = result.scalar_one()
        scopes_result = await session.execute(
            select(InvoiceItemTemplateParcel).where(InvoiceItemTemplateParcel.invoice_item_template_id == template.id)
        )
        assert len(scopes_result.scalars().all()) == 1

    # Resave with the identical, already-scoped parcel -- this is what
    # crashed in production.
    resp = await client.post(
        f"/finances/item-templates/{template.id}/edit",
        data={
            "order_number": "10", "name": "Unchanged scope template", "description": "",
            "pricing_mode": "fixed_per_parcel", "unit_price": "9.00",
            "parcel_ids": [parcel_id],
        },
    )
    assert resp.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
        scopes_result = await session.execute(
            select(InvoiceItemTemplateParcel).where(InvoiceItemTemplateParcel.invoice_item_template_id == template.id)
        )
        assert len(scopes_result.scalars().all()) == 1


async def test_run_item_update_with_unchanged_scope_does_not_500(client, admin_user):
    """Same real-world scenario as above, but for a run's own item
    definition instead of a catalog template."""
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    await _enable_finances_module()
    parcel_id = await _create_active_parcel("DEDUP-6")
    run_id = await _make_run(client)

    r_create = await client.post(
        f"/finances/runs/{run_id}/items",
        data={
            "order_number": "10", "name": "Unchanged scope run item", "description": "",
            "pricing_mode": "fixed_per_parcel", "unit_price": "9.00",
            "parcel_ids": [parcel_id],
        },
    )
    assert r_create.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(InvoiceItemDefinition).where(InvoiceItemDefinition.name == "Unchanged scope run item")
        )
        item = result.scalar_one()

    resp = await client.post(
        f"/finances/runs/{run_id}/items/{item.id}/edit",
        data={
            "order_number": "10", "name": "Unchanged scope run item", "description": "",
            "pricing_mode": "fixed_per_parcel", "unit_price": "9.00",
            "parcel_ids": [parcel_id],
        },
    )
    assert resp.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
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
