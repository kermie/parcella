"""
Issue #94: the "add from item catalog" picker on a draft run's detail
page should not offer a catalog item the run already has. There is no
stored link from an InvoiceItemDefinition back to the InvoiceItemTemplate
it was copied from (items_add_from_catalog just copies fields), so
run_detail's GET handler filters by name instead, recomputed on every
load.
"""
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import ClubSetting, InvoiceItemTemplate


async def _template_by_name(name: str) -> InvoiceItemTemplate:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(InvoiceItemTemplate).where(InvoiceItemTemplate.name == name))
        return result.scalars().one()


async def _enable_finances_module():
    async with AsyncSessionLocal() as session:
        session.add(ClubSetting(key="modul_finances", value="true", description="test"))
        await session.commit()


async def _make_run(client, year="2026"):
    r_create = await client.post("/finances/runs", data={
        "year": year, "subject": "Catalog filter test", "issued_date": f"{year}-08-01",
        "due_date": f"{year}-09-01", "footer_text": "",
    })
    assert r_create.status_code in (302, 303)
    return r_create.headers["location"].rstrip("/").split("/")[-1]


async def _make_template(client, name: str, order_number: str = "10"):
    r_template = await client.post("/finances/item-templates", data={
        "order_number": order_number, "name": name, "description": "",
        "pricing_mode": "fixed_per_parcel", "unit_price": "12.00", "applies_to_all_parcels": "on",
    })
    assert r_template.status_code in (302, 303)


async def test_used_catalog_item_disappears_from_add_from_catalog_picker(client, admin_user):
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    await _enable_finances_module()

    await _make_template(client, "Gartenwasser", "10")
    await _make_template(client, "Vereinsbeitrag", "20")

    run_id = await _make_run(client)

    r_before = await client.get(f"/finances/runs/{run_id}")
    assert r_before.status_code == 200
    assert "Gartenwasser" in r_before.text
    assert "Vereinsbeitrag" in r_before.text

    template = await _template_by_name("Gartenwasser")

    r_apply = await client.post(
        f"/finances/runs/{run_id}/items/add-from-catalog", data={"template_ids": [template.id]},
    )
    assert r_apply.status_code in (302, 303)

    r_after = await client.get(f"/finances/runs/{run_id}")
    assert r_after.status_code == 200
    assert "Vereinsbeitrag" in r_after.text, "the still-unused template must remain offered"
    # "Gartenwasser" now appears as an actual run item (in the items
    # table), but must be gone from the catalog picker's checkbox list.
    picker_section = r_after.text.split(">Add from item catalog<")[-1]
    assert "Gartenwasser" not in picker_section, "the already-added template must not be offered again"


async def test_catalog_all_used_shows_distinct_hint_from_empty_catalog(client, admin_user):
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    await _enable_finances_module()

    run_id = await _make_run(client)

    # No templates exist at all yet -> the "no catalog items yet" hint.
    r_none = await client.get(f"/finances/runs/{run_id}")
    assert "No catalog items yet" in r_none.text
    assert "already been added to this run" not in r_none.text

    await _make_template(client, "Einzige Katalogposition")
    template = await _template_by_name("Einzige Katalogposition")

    r_apply = await client.post(
        f"/finances/runs/{run_id}/items/add-from-catalog", data={"template_ids": [template.id]},
    )
    assert r_apply.status_code in (302, 303)

    # The only template now exists but is already used -> the distinct
    # "all used" hint, not the "no catalog items yet" one.
    r_all_used = await client.get(f"/finances/runs/{run_id}")
    assert "already been added to this run" in r_all_used.text
    assert "No catalog items yet" not in r_all_used.text
