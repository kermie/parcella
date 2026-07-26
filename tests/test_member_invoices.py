"""
A club member with no current parcel assignment (e.g. a supporting
member without a plot) previously could never be billed by any invoice
item, regardless of pricing mode -- compute_invoices_for_run only ever
looped over parcels. Adds a second invoiceable subject ("member
invoice") for fixed_per_person items explicitly marked
applies_to_members_without_parcel=True (default off, opt-in per item)
-- see app/invoice_generation.py's _compute_member_invoices.
"""
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import AsyncSessionLocal
from app.models import (
    Member, MemberEmail, MemberParcel, Parcel, Invoice, InvoiceItemDefinition, InvoiceItemTemplate, ClubSetting,
)


async def _enable_finances_module():
    async with AsyncSessionLocal() as session:
        session.add(ClubSetting(key="modul_finances", value="true", description="test"))
        await session.commit()


async def _make_run(client, year="2026"):
    r_create = await client.post("/finances/runs", data={
        "year": year, "subject": "Member invoice test", "issued_date": f"{year}-08-01",
        "due_date": f"{year}-09-01", "footer_text": "",
    })
    assert r_create.status_code in (302, 303)
    return r_create.headers["location"].rstrip("/").split("/")[-1]


async def test_member_without_parcel_billed_when_item_opts_in(client, admin_user):
    """The reported bug: a fixed_per_person item with
    applies_to_members_without_parcel=True must bill an active member
    who has no current parcel, as a standalone member invoice
    (member_id set, parcel_id NULL)."""
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    await _enable_finances_module()

    async with AsyncSessionLocal() as session:
        member = Member(
            first_name="Support", last_name="NoPlot",
            street="Vereinsstr 1", postal_code="12345", city="Testort",
        )
        session.add(member)
        await session.commit()
        member_id = member.id

    run_id = await _make_run(client)
    r_item = await client.post(f"/finances/runs/{run_id}/items", data={
        "order_number": "10", "name": "Membership fee", "description": "",
        "pricing_mode": "fixed_per_person", "unit_price": "25.00",
        "applies_to_all_parcels": "on", "applies_to_members_without_parcel": "on",
    })
    assert r_item.status_code in (302, 303)

    r_preview = await client.get(f"/finances/runs/{run_id}/preview")
    assert r_preview.status_code == 200
    assert "UndefinedError" not in r_preview.text
    assert "Support NoPlot" in r_preview.text

    r_preview_pdf = await client.get(f"/finances/runs/{run_id}/preview/member/{member_id}/pdf")
    assert r_preview_pdf.status_code == 200
    assert r_preview_pdf.headers["content-type"] == "application/pdf"

    r_finalize = await client.post(f"/finances/runs/{run_id}/finalize")
    assert r_finalize.status_code in (302, 303), r_finalize.headers.get("location")

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Invoice).where(Invoice.invoice_run_id == run_id))
        invoice = result.scalars().one()
    assert invoice.member_id == member_id
    assert invoice.parcel_id is None
    assert float(invoice.subtotal) == 25.0
    assert "Support NoPlot" in invoice.recipient_names

    r_pdf = await client.get(f"/finances/invoices/{invoice.id}/pdf")
    assert r_pdf.status_code == 200
    assert r_pdf.headers["content-type"] == "application/pdf"

    r_invoice_detail = await client.get(f"/finances/invoices/{invoice.id}")
    assert r_invoice_detail.status_code == 200
    assert "UndefinedError" not in r_invoice_detail.text

    r_invoice_list = await client.get("/finances/invoices")
    assert r_invoice_list.status_code == 200
    assert "UndefinedError" not in r_invoice_list.text
    assert "Support NoPlot" in r_invoice_list.text


async def test_member_without_parcel_excluded_when_item_does_not_opt_in(client, admin_user):
    """Regression safety for the default: an ordinary fixed_per_person
    item (applies_to_members_without_parcel left off, the default)
    must NOT pick up a member with no parcel -- upgrading must never
    silently change what an existing run bills."""
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    await _enable_finances_module()

    async with AsyncSessionLocal() as session:
        member = Member(
            first_name="Support", last_name="StillExcluded",
            street="Vereinsstr 1", postal_code="12345", city="Testort",
        )
        session.add(member)
        await session.commit()

    run_id = await _make_run(client, year="2027")
    await client.post(f"/finances/runs/{run_id}/items", data={
        "order_number": "10", "name": "Membership fee", "description": "",
        "pricing_mode": "fixed_per_person", "unit_price": "25.00",
        "applies_to_all_parcels": "on",
    })

    r_preview = await client.get(f"/finances/runs/{run_id}/preview")
    assert r_preview.status_code == 200
    assert "Support StillExcluded" not in r_preview.text

    r_finalize = await client.post(f"/finances/runs/{run_id}/finalize")
    assert r_finalize.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Invoice).where(Invoice.invoice_run_id == run_id))
        invoices = result.scalars().all()
    assert len(invoices) == 0, "no parcel and the item didn't opt in -- must produce no invoice at all"


async def test_member_invoice_delivered_by_email_directly(client, admin_user, monkeypatch):
    """Email delivery for a member invoice resolves via the member
    itself (not via any MemberParcel household lookup)."""
    sent_to = []

    async def fake_send_email(recipient, subject, html_body, text_body=None, db=None, attachments=None):
        sent_to.append(recipient)
        return True

    monkeypatch.setattr("app.invoice_delivery.send_email", fake_send_email)

    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    await _enable_finances_module()

    async with AsyncSessionLocal() as session:
        member = Member(
            first_name="Support", last_name="Emailed",
            street="Vereinsstr 1", postal_code="12345", city="Testort",
            email_notifications=True,
        )
        session.add(member)
        await session.flush()
        session.add(MemberEmail(member_id=member.id, address="support@example.com", is_primary=True))
        await session.commit()

    run_id = await _make_run(client, year="2028")
    await client.post(f"/finances/runs/{run_id}/items", data={
        "order_number": "10", "name": "Membership fee", "description": "",
        "pricing_mode": "fixed_per_person", "unit_price": "25.00",
        "applies_to_all_parcels": "on", "applies_to_members_without_parcel": "on",
    })
    await client.post(f"/finances/runs/{run_id}/finalize")

    r_deliver = await client.post(f"/finances/runs/{run_id}/deliver")
    assert "emailed=1" in r_deliver.headers["location"]
    assert sent_to == ["support@example.com"]

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Invoice).options(selectinload(Invoice.member)).where(Invoice.invoice_run_id == run_id)
        )
        invoice = result.scalars().one()
    assert invoice.emailed_at is not None
    assert invoice.printed_at is None


async def test_catalog_item_scoped_to_members_only_does_not_bill_parcel_tenants(client, admin_user):
    """Regression for the exact reported bug: items_add_from_catalog
    used to hardcode applies_to_all_parcels=True for every catalog
    item, so a fixed_per_person template meant to bill ONLY members
    without a parcel (applies_to_all_parcels unchecked,
    applies_to_members_without_parcel checked) still also billed every
    parcel tenant. Must produce exactly one invoice -- the member
    without a parcel -- and none for the parcel tenant."""
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    await _enable_finances_module()

    async with AsyncSessionLocal() as session:
        tenant = Member(
            first_name="Parcel", last_name="Tenant",
            street="Gartenweg 1", postal_code="12345", city="Testort",
        )
        supporter = Member(
            first_name="Support", last_name="NoParcelAtAll",
            street="Vereinsstr 1", postal_code="12345", city="Testort",
        )
        parcel = Parcel(plot_number="CATALOGSCOPE-1", area_sqm=100)
        session.add_all([tenant, supporter, parcel])
        await session.flush()
        session.add(MemberParcel(member_id=tenant.id, parcel_id=parcel.id, is_invoice_address=True))
        await session.commit()

    r_template = await client.post("/finances/item-templates", data={
        "order_number": "10", "name": "Fördermitgliedsbeitrag", "description": "",
        "pricing_mode": "fixed_per_person", "unit_price": "30.00",
        # applies_to_all_parcels deliberately omitted (unchecked) --
        # this template must reach ONLY members without a parcel.
        "applies_to_members_without_parcel": "on",
    })
    assert r_template.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(InvoiceItemTemplate).where(InvoiceItemTemplate.name == "Fördermitgliedsbeitrag")
        )
        template = result.scalars().one()
    assert template.applies_to_all_parcels is False

    run_id = await _make_run(client, year="2029")
    r_apply = await client.post(
        f"/finances/runs/{run_id}/items/add-from-catalog", data={"template_ids": [template.id]},
    )
    assert r_apply.status_code in (302, 303)

    r_preview = await client.get(f"/finances/runs/{run_id}/preview")
    assert r_preview.status_code == 200
    assert "Support NoParcelAtAll" in r_preview.text
    assert "Parcel Tenant" not in r_preview.text

    r_finalize = await client.post(f"/finances/runs/{run_id}/finalize")
    assert r_finalize.status_code in (302, 303), r_finalize.headers.get("location")

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Invoice).where(Invoice.invoice_run_id == run_id))
        invoices = result.scalars().all()
    assert len(invoices) == 1, "only the member without a parcel should be billed, not the parcel tenant"
    assert invoices[0].member_id is not None
    assert invoices[0].parcel_id is None
    assert "Support NoParcelAtAll" in invoices[0].recipient_names


async def test_item_parcel_scope_stays_editable_before_finalize(client, admin_user):
    """A member asked explicitly: the specific-parcel picker on an
    item must stay editable at any time before the run is finalized,
    not just at creation. Covers: create scoped to one parcel, edit to
    swap to a different parcel, then edit again to switch to "all
    parcels" (which must clear the now-stale specific scope)."""
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    await _enable_finances_module()

    async with AsyncSessionLocal() as session:
        parcel_a = Parcel(plot_number="SCOPE-EDIT-A", area_sqm=100)
        parcel_b = Parcel(plot_number="SCOPE-EDIT-B", area_sqm=100)
        session.add_all([parcel_a, parcel_b])
        await session.commit()
        parcel_a_id, parcel_b_id = parcel_a.id, parcel_b.id

    run_id = await _make_run(client, year="2030")
    r_item = await client.post(f"/finances/runs/{run_id}/items", data={
        "order_number": "10", "name": "Scoped fee", "description": "",
        "pricing_mode": "fixed_per_parcel", "unit_price": "5.00",
        "parcel_ids": [parcel_a_id],
    })
    assert r_item.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(InvoiceItemDefinition)
            .options(selectinload(InvoiceItemDefinition.parcel_scopes))
            .where(InvoiceItemDefinition.invoice_run_id == run_id)
        )
        item = result.scalars().one()
    assert item.applies_to_all_parcels is False
    assert {s.parcel_id for s in item.parcel_scopes} == {parcel_a_id}

    # Edit: swap the scope from parcel A to parcel B.
    r_edit1 = await client.post(f"/finances/runs/{run_id}/items/{item.id}/edit", data={
        "order_number": "10", "name": "Scoped fee", "description": "",
        "pricing_mode": "fixed_per_parcel", "unit_price": "5.00",
        "parcel_ids": [parcel_b_id],
    })
    assert r_edit1.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(InvoiceItemDefinition)
            .options(selectinload(InvoiceItemDefinition.parcel_scopes))
            .where(InvoiceItemDefinition.id == item.id)
        )
        item = result.scalars().one()
    assert {s.parcel_id for s in item.parcel_scopes} == {parcel_b_id}, "scope must be replaced, not merged"

    # Edit again: switch to "all parcels" -- the stale specific scope
    # must be cleared, not left lingering underneath.
    r_edit2 = await client.post(f"/finances/runs/{run_id}/items/{item.id}/edit", data={
        "order_number": "10", "name": "Scoped fee", "description": "",
        "pricing_mode": "fixed_per_parcel", "unit_price": "5.00",
        "applies_to_all_parcels": "on",
    })
    assert r_edit2.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(InvoiceItemDefinition)
            .options(selectinload(InvoiceItemDefinition.parcel_scopes))
            .where(InvoiceItemDefinition.id == item.id)
        )
        item = result.scalars().one()
    assert item.applies_to_all_parcels is True
    assert item.parcel_scopes == []


async def test_item_template_parcel_scope_editable_and_copied_to_run(client, admin_user):
    """Explicit request: the item catalog needs the same specific-
    parcel picker the run's own items have. Covers: create a template
    scoped to one parcel, edit it to a different parcel, then apply it
    from the catalog to a run and confirm the exact scope is copied
    onto the new item (and stays independently editable there)."""
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    await _enable_finances_module()

    async with AsyncSessionLocal() as session:
        parcel_a = Parcel(plot_number="TPL-SCOPE-A", area_sqm=100)
        parcel_b = Parcel(plot_number="TPL-SCOPE-B", area_sqm=100)
        session.add_all([parcel_a, parcel_b])
        await session.commit()
        parcel_a_id, parcel_b_id = parcel_a.id, parcel_b.id

    r_template = await client.post("/finances/item-templates", data={
        "order_number": "10", "name": "Scoped template", "description": "",
        "pricing_mode": "fixed_per_parcel", "unit_price": "7.00",
        "parcel_ids": [parcel_a_id],
    })
    assert r_template.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(InvoiceItemTemplate)
            .options(selectinload(InvoiceItemTemplate.parcel_scopes))
            .where(InvoiceItemTemplate.name == "Scoped template")
        )
        template = result.scalars().one()
    assert template.applies_to_all_parcels is False
    assert {s.parcel_id for s in template.parcel_scopes} == {parcel_a_id}

    # Edit the template: swap the scope from parcel A to parcel B.
    r_edit = await client.post(f"/finances/item-templates/{template.id}/edit", data={
        "order_number": "10", "name": "Scoped template", "description": "",
        "pricing_mode": "fixed_per_parcel", "unit_price": "7.00",
        "parcel_ids": [parcel_b_id],
    })
    assert r_edit.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(InvoiceItemTemplate)
            .options(selectinload(InvoiceItemTemplate.parcel_scopes))
            .where(InvoiceItemTemplate.id == template.id)
        )
        template = result.scalars().one()
    assert {s.parcel_id for s in template.parcel_scopes} == {parcel_b_id}, "scope must be replaced, not merged"

    # Apply from the catalog to a run -- the exact scope must transfer.
    run_id = await _make_run(client, year="2031")
    r_apply = await client.post(
        f"/finances/runs/{run_id}/items/add-from-catalog", data={"template_ids": [template.id]},
    )
    assert r_apply.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(InvoiceItemDefinition)
            .options(selectinload(InvoiceItemDefinition.parcel_scopes))
            .where(InvoiceItemDefinition.invoice_run_id == run_id)
        )
        item = result.scalars().one()
    assert item.applies_to_all_parcels is False
    assert {s.parcel_id for s in item.parcel_scopes} == {parcel_b_id}
