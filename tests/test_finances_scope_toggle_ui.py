"""
Regression test for issue #84: the "applies to" form-switch on the
item-templates list and on a run's items table had no visible gap
between the checkbox and its label. Root cause was the row-level
sync() JS setting these toggle-wraps to `display: flex`, which
defeats Bootstrap's float+negative-margin spacing technique for
form-switch (see ADR/commit a33a4cd, issue #79 -- the same bug fixed
once already). It regressed when issue #83's "work-hours shortfall"
pricing mode touched this same sync() function and re-introduced
'flex' instead of 'block'. Assert the served page never emits the
buggy `'flex'` value for these two toggle-wraps again.
"""
from app.database import AsyncSessionLocal
from app.models import ClubSetting


async def _enable_finances_module():
    async with AsyncSessionLocal() as session:
        session.add(ClubSetting(key="modul_finances", value="true", description="test"))
        await session.commit()


async def _make_run(client, year="2026"):
    r_create = await client.post("/finances/runs", data={
        "year": year, "subject": "Toggle spacing test", "issued_date": f"{year}-08-01",
        "due_date": f"{year}-09-01", "footer_text": "",
    })
    assert r_create.status_code in (302, 303)
    return r_create.headers["location"].rstrip("/").split("/")[-1]


def _assert_toggle_wrap_uses_block_not_flex(html):
    assert "'flex'" not in html, (
        "existing-row applies-to toggle-wrap must use 'block' display, not 'flex' "
        "(issue #84/#79: 'flex' breaks the form-switch checkbox-to-label spacing)"
    )
    assert "parcelToggleWrap.style.display = (isPerson || isScopeAutomatic) ? 'none' : 'block';" in html
    assert "memberToggleWrap.style.display = (isPerson && !isScopeAutomatic) ? 'block' : 'none';" in html


async def test_item_template_list_toggle_wrap_spacing(client, admin_user):
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    await _enable_finances_module()

    r_template = await client.post("/finances/item-templates", data={
        "order_number": "10", "name": "Spacing check template", "description": "",
        "pricing_mode": "fixed_per_parcel", "unit_price": "9.00",
    })
    assert r_template.status_code in (302, 303)

    r_list = await client.get("/finances/item-templates")
    assert r_list.status_code == 200
    assert "UndefinedError" not in r_list.text
    _assert_toggle_wrap_uses_block_not_flex(r_list.text)


async def test_run_detail_toggle_wrap_spacing(client, admin_user):
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    await _enable_finances_module()

    run_id = await _make_run(client)
    r_item = await client.post(f"/finances/runs/{run_id}/items", data={
        "order_number": "10", "name": "Spacing check item", "description": "",
        "pricing_mode": "fixed_per_parcel", "unit_price": "9.00",
    })
    assert r_item.status_code in (302, 303)

    r_detail = await client.get(f"/finances/runs/{run_id}")
    assert r_detail.status_code == 200
    assert "UndefinedError" not in r_detail.text
    _assert_toggle_wrap_uses_block_not_flex(r_detail.text)
