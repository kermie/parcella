"""
Invoice items split into two independent, symmetric targeting
mechanisms (a member asked for this explicitly after the previous
"also bill members without a parcel" bolt-on produced nonsensical
combinations like "select a parcel AND bill non-tenant members"):
plot-scoped pricing modes keep applies_to_all_parcels/parcel_scopes
unchanged; fixed_per_person gets its own mirror-image
applies_to_all_members/member_scopes, billed to targeted members
directly via a standalone member invoice (Invoice.member_id),
regardless of parcel status -- see
app/invoice_generation.py's _compute_member_invoices.
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


async def test_member_billed_via_all_members_scope_regardless_of_parcel(client, admin_user):
    """A fixed_per_person item with applies_to_all_members=True bills
    every active member once, directly, regardless of whether they
    currently have a parcel -- the reported bug: a member with no
    parcel was structurally unreachable by any pricing mode before."""
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    await _enable_finances_module()

    async with AsyncSessionLocal() as session:
        no_parcel_member = Member(
            first_name="Support", last_name="NoPlot",
            street="Vereinsstr 1", postal_code="12345", city="Testort",
        )
        tenant = Member(
            first_name="Parcel", last_name="Tenant",
            street="Gartenweg 1", postal_code="12345", city="Testort",
        )
        parcel = Parcel(plot_number="ALLMEMBERS-1", area_sqm=100)
        session.add_all([no_parcel_member, tenant, parcel])
        await session.flush()
        session.add(MemberParcel(member_id=tenant.id, parcel_id=parcel.id, is_invoice_address=True))
        await session.commit()
        member_id = no_parcel_member.id

    run_id = await _make_run(client)
    r_item = await client.post(f"/finances/runs/{run_id}/items", data={
        "order_number": "10", "name": "Membership fee", "description": "",
        "pricing_mode": "fixed_per_person", "unit_price": "25.00",
        "applies_to_all_members": "on",
    })
    assert r_item.status_code in (302, 303)

    r_preview = await client.get(f"/finances/runs/{run_id}/preview")
    assert r_preview.status_code == 200
    assert "UndefinedError" not in r_preview.text
    assert "Support NoPlot" in r_preview.text
    assert "Parcel Tenant" in r_preview.text

    r_preview_pdf = await client.get(f"/finances/runs/{run_id}/preview/member/{member_id}/pdf")
    assert r_preview_pdf.status_code == 200
    assert r_preview_pdf.headers["content-type"] == "application/pdf"

    r_finalize = await client.post(f"/finances/runs/{run_id}/finalize")
    assert r_finalize.status_code in (302, 303), r_finalize.headers.get("location")

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Invoice).where(Invoice.invoice_run_id == run_id))
        invoices = result.scalars().all()
    assert len(invoices) == 2, "both members billed, each as their own invoice, none tied to the parcel"
    assert all(i.member_id is not None and i.parcel_id is None for i in invoices)

    member_invoice = next(i for i in invoices if i.member_id == member_id)
    assert float(member_invoice.subtotal) == 25.0
    assert "Support NoPlot" in member_invoice.recipient_names

    r_pdf = await client.get(f"/finances/invoices/{member_invoice.id}/pdf")
    assert r_pdf.status_code == 200
    assert r_pdf.headers["content-type"] == "application/pdf"

    r_invoice_detail = await client.get(f"/finances/invoices/{member_invoice.id}")
    assert r_invoice_detail.status_code == 200
    assert "UndefinedError" not in r_invoice_detail.text

    r_invoice_list = await client.get("/finances/invoices")
    assert r_invoice_list.status_code == 200
    assert "UndefinedError" not in r_invoice_list.text
    assert "Support NoPlot" in r_invoice_list.text


async def test_member_scope_restricted_to_specific_members_excludes_others(client, admin_user):
    """A fixed_per_person item scoped to specific member_ids (not
    applies_to_all_members) bills only those members -- inclusion is
    purely by selection, never by parcel status."""
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    await _enable_finances_module()

    async with AsyncSessionLocal() as session:
        targeted = Member(
            first_name="Targeted", last_name="Member",
            street="Vereinsstr 1", postal_code="12345", city="Testort",
        )
        untargeted = Member(
            first_name="Untargeted", last_name="Member",
            street="Vereinsstr 2", postal_code="12345", city="Testort",
        )
        session.add_all([targeted, untargeted])
        await session.commit()
        targeted_id = targeted.id

    run_id = await _make_run(client, year="2027")
    await client.post(f"/finances/runs/{run_id}/items", data={
        "order_number": "10", "name": "Selective fee", "description": "",
        "pricing_mode": "fixed_per_person", "unit_price": "25.00",
        "member_ids": [targeted_id],
    })

    r_preview = await client.get(f"/finances/runs/{run_id}/preview")
    assert r_preview.status_code == 200
    assert "Targeted Member" in r_preview.text
    assert "Untargeted Member" not in r_preview.text

    r_finalize = await client.post(f"/finances/runs/{run_id}/finalize")
    assert r_finalize.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Invoice).where(Invoice.invoice_run_id == run_id))
        invoices = result.scalars().all()
    assert len(invoices) == 1
    assert invoices[0].member_id == targeted_id


async def test_no_member_scope_selected_produces_no_invoice(client, admin_user):
    """Regression safety: a fixed_per_person item with
    applies_to_all_members off and no member_ids selected bills
    nobody -- upgrading/misconfiguring must never silently bill
    everyone by accident."""
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    await _enable_finances_module()

    async with AsyncSessionLocal() as session:
        member = Member(
            first_name="Nobody", last_name="Targeted",
            street="Vereinsstr 1", postal_code="12345", city="Testort",
        )
        session.add(member)
        await session.commit()

    run_id = await _make_run(client, year="2032")
    await client.post(f"/finances/runs/{run_id}/items", data={
        "order_number": "10", "name": "Unconfigured fee", "description": "",
        "pricing_mode": "fixed_per_person", "unit_price": "25.00",
    })

    r_finalize = await client.post(f"/finances/runs/{run_id}/finalize")
    assert r_finalize.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Invoice).where(Invoice.invoice_run_id == run_id))
        invoices = result.scalars().all()
    assert len(invoices) == 0


async def test_two_person_items_targeting_same_member_aggregate_onto_one_invoice(client, admin_user):
    """A member targeted by two separate fixed_per_person items gets
    ONE invoice with two line items, not two invoices -- mirrors how a
    parcel's multiple applicable items already merge onto one invoice."""
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    await _enable_finances_module()

    async with AsyncSessionLocal() as session:
        member = Member(
            first_name="Double", last_name="Billed",
            street="Vereinsstr 1", postal_code="12345", city="Testort",
        )
        session.add(member)
        await session.commit()
        member_id = member.id

    run_id = await _make_run(client, year="2033")
    await client.post(f"/finances/runs/{run_id}/items", data={
        "order_number": "10", "name": "Membership fee", "description": "",
        "pricing_mode": "fixed_per_person", "unit_price": "25.00",
        "applies_to_all_members": "on",
    })
    await client.post(f"/finances/runs/{run_id}/items", data={
        "order_number": "20", "name": "Honorary supplement", "description": "",
        "pricing_mode": "fixed_per_person", "unit_price": "10.00",
        "member_ids": [member_id],
    })

    r_finalize = await client.post(f"/finances/runs/{run_id}/finalize")
    assert r_finalize.status_code in (302, 303), r_finalize.headers.get("location")

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Invoice).where(Invoice.invoice_run_id == run_id))
        invoices = result.scalars().all()
    assert len(invoices) == 1, "one member, two applicable items -- must merge onto a single invoice"
    invoice = invoices[0]
    assert float(invoice.subtotal) == 35.0

    async with AsyncSessionLocal() as session:
        invoice = await session.get(Invoice, invoice.id)
        await session.refresh(invoice, attribute_names=["line_items"])
        assert len(invoice.line_items) == 2


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
        "applies_to_all_members": "on",
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


async def test_catalog_item_scoped_to_specific_members_bills_only_them(client, admin_user):
    """The item catalog's member-scope picker: a fixed_per_person
    template scoped to a specific member and applied to a run bills
    only that member, regardless of any parcel tenants that also
    exist on the run."""
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
        supporter_id = supporter.id

    r_template = await client.post("/finances/item-templates", data={
        "order_number": "10", "name": "Fördermitgliedsbeitrag", "description": "",
        "pricing_mode": "fixed_per_person", "unit_price": "30.00",
        "member_ids": [supporter_id],
    })
    assert r_template.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(InvoiceItemTemplate)
            .options(selectinload(InvoiceItemTemplate.member_scopes))
            .where(InvoiceItemTemplate.name == "Fördermitgliedsbeitrag")
        )
        template = result.scalars().one()
    assert template.applies_to_all_members is False
    assert {s.member_id for s in template.member_scopes} == {supporter_id}

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
    assert len(invoices) == 1, "only the targeted member should be billed, not the parcel tenant"
    assert invoices[0].member_id == supporter_id
    assert invoices[0].parcel_id is None


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


async def test_item_member_scope_stays_editable_before_finalize(client, admin_user):
    """Same as the parcel-scope editability test, but for a
    fixed_per_person item's member scope."""
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    await _enable_finances_module()

    async with AsyncSessionLocal() as session:
        member_a = Member(
            first_name="Edit", last_name="MemberA",
            street="Vereinsstr 1", postal_code="12345", city="Testort",
        )
        member_b = Member(
            first_name="Edit", last_name="MemberB",
            street="Vereinsstr 2", postal_code="12345", city="Testort",
        )
        session.add_all([member_a, member_b])
        await session.commit()
        member_a_id, member_b_id = member_a.id, member_b.id

    run_id = await _make_run(client, year="2034")
    r_item = await client.post(f"/finances/runs/{run_id}/items", data={
        "order_number": "10", "name": "Scoped person fee", "description": "",
        "pricing_mode": "fixed_per_person", "unit_price": "5.00",
        "member_ids": [member_a_id],
    })
    assert r_item.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(InvoiceItemDefinition)
            .options(selectinload(InvoiceItemDefinition.member_scopes))
            .where(InvoiceItemDefinition.invoice_run_id == run_id)
        )
        item = result.scalars().one()
    assert item.applies_to_all_members is False
    assert {s.member_id for s in item.member_scopes} == {member_a_id}

    r_run_detail = await client.get(f"/finances/runs/{run_id}")
    assert r_run_detail.status_code == 200
    assert "UndefinedError" not in r_run_detail.text
    assert "Edit MemberA" in r_run_detail.text

    # Edit: swap the scope from member A to member B.
    r_edit1 = await client.post(f"/finances/runs/{run_id}/items/{item.id}/edit", data={
        "order_number": "10", "name": "Scoped person fee", "description": "",
        "pricing_mode": "fixed_per_person", "unit_price": "5.00",
        "member_ids": [member_b_id],
    })
    assert r_edit1.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(InvoiceItemDefinition)
            .options(selectinload(InvoiceItemDefinition.member_scopes))
            .where(InvoiceItemDefinition.id == item.id)
        )
        item = result.scalars().one()
    assert {s.member_id for s in item.member_scopes} == {member_b_id}, "scope must be replaced, not merged"

    # Edit again: switch to "all members" -- the stale specific scope
    # must be cleared.
    r_edit2 = await client.post(f"/finances/runs/{run_id}/items/{item.id}/edit", data={
        "order_number": "10", "name": "Scoped person fee", "description": "",
        "pricing_mode": "fixed_per_person", "unit_price": "5.00",
        "applies_to_all_members": "on",
    })
    assert r_edit2.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(InvoiceItemDefinition)
            .options(selectinload(InvoiceItemDefinition.member_scopes))
            .where(InvoiceItemDefinition.id == item.id)
        )
        item = result.scalars().one()
    assert item.applies_to_all_members is True
    assert item.member_scopes == []


async def test_switching_back_to_all_parcels_bills_every_parcel_not_just_the_stale_scope(client, admin_user):
    """A member asked explicitly: if a parcel was picked via the
    specific-parcel picker and the item is then switched back to
    "applies to every parcel", every parcel must really be billed --
    not just the one that happened to still be checked in the (now
    hidden) picker grid. Submits parcel_ids alongside
    applies_to_all_parcels=on, simulating exactly what a browser would
    send if a previously-checked box is still checked while its picker
    is collapsed/hidden, and confirms the DB scope is cleared AND that
    generating invoices actually covers every occupied parcel, not just
    the stale one."""
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    await _enable_finances_module()

    async with AsyncSessionLocal() as session:
        parcels = [Parcel(plot_number=f"ALLBACK-{i}", area_sqm=100) for i in "ABC"]
        session.add_all(parcels)
        await session.flush()
        for i, parcel in enumerate(parcels):
            tenant = Member(
                first_name="Tenant", last_name=f"AllBack{i}",
                street=f"Gartenweg {i}", postal_code="12345", city="Testort",
            )
            session.add(tenant)
            await session.flush()
            session.add(MemberParcel(member_id=tenant.id, parcel_id=parcel.id, is_invoice_address=True))
        await session.commit()
        parcel_a_id = parcels[0].id
        all_plot_numbers = {p.plot_number for p in parcels}

    run_id = await _make_run(client, year="2038")
    r_item = await client.post(f"/finances/runs/{run_id}/items", data={
        "order_number": "10", "name": "Scope-back fee", "description": "",
        "pricing_mode": "fixed_per_parcel", "unit_price": "5.00",
        "parcel_ids": [parcel_a_id],
    })
    assert r_item.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(InvoiceItemDefinition)
            .where(InvoiceItemDefinition.invoice_run_id == run_id)
        )
        item = result.scalars().one()

    # Switch back to "all parcels", but still submit parcel_ids=[A] --
    # exactly what happens if the browser leaves the hidden checkbox
    # checked. The "all parcels" toggle must win regardless.
    r_edit = await client.post(f"/finances/runs/{run_id}/items/{item.id}/edit", data={
        "order_number": "10", "name": "Scope-back fee", "description": "",
        "pricing_mode": "fixed_per_parcel", "unit_price": "5.00",
        "applies_to_all_parcels": "on", "parcel_ids": [parcel_a_id],
    })
    assert r_edit.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(InvoiceItemDefinition)
            .options(selectinload(InvoiceItemDefinition.parcel_scopes))
            .where(InvoiceItemDefinition.id == item.id)
        )
        item = result.scalars().one()
    assert item.applies_to_all_parcels is True
    assert item.parcel_scopes == [], "a stale parcel_ids submission must not survive switching to all-parcels"

    r_preview = await client.get(f"/finances/runs/{run_id}/preview")
    assert r_preview.status_code == 200
    for plot_number in all_plot_numbers:
        assert plot_number in r_preview.text, f"{plot_number} must be billed once 'all parcels' is on"


async def test_switching_back_to_all_members_bills_every_member_not_just_the_stale_scope(client, admin_user):
    """Same guarantee as the parcel version, for fixed_per_person's
    member scope: switching back to "applies to every member" must
    bill every active member, not just whichever member was still
    checked in the hidden picker grid."""
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    await _enable_finances_module()

    async with AsyncSessionLocal() as session:
        members = [
            Member(first_name="AllBack", last_name=f"Member{i}", street=f"Vereinsstr {i}",
                   postal_code="12345", city="Testort")
            for i in "ABC"
        ]
        session.add_all(members)
        await session.commit()
        member_a_id = members[0].id
        all_names = {m.full_name for m in members}

    run_id = await _make_run(client, year="2039")
    r_item = await client.post(f"/finances/runs/{run_id}/items", data={
        "order_number": "10", "name": "Scope-back person fee", "description": "",
        "pricing_mode": "fixed_per_person", "unit_price": "5.00",
        "member_ids": [member_a_id],
    })
    assert r_item.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(InvoiceItemDefinition)
            .where(InvoiceItemDefinition.invoice_run_id == run_id)
        )
        item = result.scalars().one()

    r_edit = await client.post(f"/finances/runs/{run_id}/items/{item.id}/edit", data={
        "order_number": "10", "name": "Scope-back person fee", "description": "",
        "pricing_mode": "fixed_per_person", "unit_price": "5.00",
        "applies_to_all_members": "on", "member_ids": [member_a_id],
    })
    assert r_edit.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(InvoiceItemDefinition)
            .options(selectinload(InvoiceItemDefinition.member_scopes))
            .where(InvoiceItemDefinition.id == item.id)
        )
        item = result.scalars().one()
    assert item.applies_to_all_members is True
    assert item.member_scopes == [], "a stale member_ids submission must not survive switching to all-members"

    r_preview = await client.get(f"/finances/runs/{run_id}/preview")
    assert r_preview.status_code == 200
    for name in all_names:
        assert name in r_preview.text, f"{name} must be billed once 'all members' is on"


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


async def test_item_template_member_scope_editable_and_copied_to_run(client, admin_user):
    """Same as the parcel-scope template test, but for a
    fixed_per_person template's member scope."""
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    await _enable_finances_module()

    async with AsyncSessionLocal() as session:
        member_a = Member(
            first_name="Tpl", last_name="MemberA",
            street="Vereinsstr 1", postal_code="12345", city="Testort",
        )
        member_b = Member(
            first_name="Tpl", last_name="MemberB",
            street="Vereinsstr 2", postal_code="12345", city="Testort",
        )
        session.add_all([member_a, member_b])
        await session.commit()
        member_a_id, member_b_id = member_a.id, member_b.id

    r_template = await client.post("/finances/item-templates", data={
        "order_number": "10", "name": "Member scoped template", "description": "",
        "pricing_mode": "fixed_per_person", "unit_price": "9.00",
        "member_ids": [member_a_id],
    })
    assert r_template.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(InvoiceItemTemplate)
            .options(selectinload(InvoiceItemTemplate.member_scopes))
            .where(InvoiceItemTemplate.name == "Member scoped template")
        )
        template = result.scalars().one()
    assert template.applies_to_all_members is False
    assert {s.member_id for s in template.member_scopes} == {member_a_id}

    r_list = await client.get("/finances/item-templates")
    assert r_list.status_code == 200
    assert "UndefinedError" not in r_list.text
    assert "Tpl MemberA" in r_list.text

    # Edit the template: swap the scope from member A to member B.
    r_edit = await client.post(f"/finances/item-templates/{template.id}/edit", data={
        "order_number": "10", "name": "Member scoped template", "description": "",
        "pricing_mode": "fixed_per_person", "unit_price": "9.00",
        "member_ids": [member_b_id],
    })
    assert r_edit.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(InvoiceItemTemplate)
            .options(selectinload(InvoiceItemTemplate.member_scopes))
            .where(InvoiceItemTemplate.id == template.id)
        )
        template = result.scalars().one()
    assert {s.member_id for s in template.member_scopes} == {member_b_id}, "scope must be replaced, not merged"

    # Apply from the catalog to a run -- the exact scope must transfer.
    run_id = await _make_run(client, year="2035")
    r_apply = await client.post(
        f"/finances/runs/{run_id}/items/add-from-catalog", data={"template_ids": [template.id]},
    )
    assert r_apply.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(InvoiceItemDefinition)
            .options(selectinload(InvoiceItemDefinition.member_scopes))
            .where(InvoiceItemDefinition.invoice_run_id == run_id)
        )
        item = result.scalars().one()
    assert item.applies_to_all_members is False
    assert {s.member_id for s in item.member_scopes} == {member_b_id}
