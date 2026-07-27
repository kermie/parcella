"""
Issue #96: on a draft run's "add from item catalog" picker, every
listed template's checkbox should be checked by default, so applying
the whole catalog to a run is "submit" rather than "check every box
first" -- a club still unchecks the few items it doesn't want for that
particular run.
"""
import re

from app.database import AsyncSessionLocal
from app.models import ClubSetting


async def _enable_finances_module():
    async with AsyncSessionLocal() as session:
        session.add(ClubSetting(key="modul_finances", value="true", description="test"))
        await session.commit()


async def _make_run(client, year="2026"):
    r_create = await client.post("/finances/runs", data={
        "year": year, "subject": "Catalog checked-by-default test", "issued_date": f"{year}-08-01",
        "due_date": f"{year}-09-01", "footer_text": "",
    })
    assert r_create.status_code in (302, 303)
    return r_create.headers["location"].rstrip("/").split("/")[-1]


async def test_catalog_picker_checkboxes_are_checked_by_default(client, admin_user):
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    await _enable_finances_module()

    for name, order in [("Wasserzins", "10"), ("Vereinsbeitrag", "20")]:
        r_template = await client.post("/finances/item-templates", data={
            "order_number": order, "name": name, "description": "",
            "pricing_mode": "fixed_per_parcel", "unit_price": "12.00", "applies_to_all_parcels": "on",
        })
        assert r_template.status_code in (302, 303)

    run_id = await _make_run(client)
    r_detail = await client.get(f"/finances/runs/{run_id}")
    assert r_detail.status_code == 200

    checkboxes = re.findall(r'<input type="checkbox" name="template_ids"[^>]*>', r_detail.text)
    assert len(checkboxes) == 2, "expected both catalog templates to be offered"
    assert all("checked" in cb for cb in checkboxes), (
        "every catalog item's checkbox must be checked by default: " + repr(checkboxes)
    )
