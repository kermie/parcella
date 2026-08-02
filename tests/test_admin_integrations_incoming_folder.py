"""
Issue #191: the incoming invoices shared cloud folder setting moved
from /finances/incoming-invoices to /admin/integrations (the
"Nextcloud" card, renamed to cover parcel documents & incoming
invoices) -- it's a system-admin-level setting like every other
integration credential, not something to configure from the finances
module's own page.
"""
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import ClubSetting


async def web_login(client, email: str = "admin@example.com", password: str = "testpasswort123") -> None:
    response = await client.post("/auth/login", data={"email": email, "password": password})
    assert response.status_code in (302, 303)


async def test_incoming_invoices_folder_no_longer_configurable_from_finances_page(client, admin_user):
    await web_login(client)
    async with AsyncSessionLocal() as session:
        session.add(ClubSetting(key="modul_finances", value="true", description="test"))
        session.add(ClubSetting(key="modul_cloud_storage", value="true", description="test"))
        await session.commit()

    response = await client.get("/finances/incoming-invoices")
    assert response.status_code == 200
    assert "/finances/incoming-invoices/cloud-folder" not in response.text


async def test_admin_can_save_incoming_invoices_folder(client, admin_user):
    await web_login(client)
    async with AsyncSessionLocal() as session:
        session.add(ClubSetting(key="modul_cloud_storage", value="true", description="test"))
        await session.commit()

    response = await client.post(
        "/admin/integrations/nextcloud/incoming-invoices-folder", data={"relative_path": "Rechnungen/Eingang"},
    )
    assert response.status_code in (302, 303)
    assert "incoming_invoices_folder_saved" in response.headers["location"]

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ClubSetting).where(ClubSetting.key == "incoming_invoices_cloud_folder")
        )
        entry = result.scalar_one()
        assert entry.value == "Rechnungen/Eingang"


async def test_admin_integrations_page_shows_incoming_invoices_folder_section(client, admin_user):
    await web_login(client)
    async with AsyncSessionLocal() as session:
        session.add(ClubSetting(key="modul_cloud_storage", value="true", description="test"))
        session.add(ClubSetting(
            key="incoming_invoices_cloud_folder", value="Rechnungen/Eingang", description="test",
        ))
        await session.commit()

    response = await client.get("/admin/integrations")
    assert response.status_code == 200
    assert "Rechnungen/Eingang" in response.text
    assert "parcel documents &amp; incoming invoices" in response.text.lower() or "incoming invoices" in response.text.lower()


async def test_incoming_invoices_folder_rejects_path_traversal(client, admin_user):
    await web_login(client)
    async with AsyncSessionLocal() as session:
        session.add(ClubSetting(key="modul_cloud_storage", value="true", description="test"))
        await session.commit()

    response = await client.post(
        "/admin/integrations/nextcloud/incoming-invoices-folder", data={"relative_path": "../etc"},
    )
    assert response.status_code in (302, 303)
    assert "incoming_invoices_folder_error" in response.headers["location"]

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ClubSetting).where(ClubSetting.key == "incoming_invoices_cloud_folder")
        )
        assert result.scalar_one_or_none() is None
