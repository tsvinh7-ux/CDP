from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from cdp_core import security
from cdp_core.identity import find_by_partner
from cdp_core.jobs import refresh_tenant
from cdp_core.models import Activation, MergeSuggestion, Profile
from cdp_core.pipeline import ingest_odoo_batch, ingest_web_batch

from .conftest import SECRET, msg, partner_msg

CLINIC, OWNER, STAFF = 10, 11, 12


def seed_clinic(session, tenant):
    return ingest_odoo_batch(session, tenant, [
        partner_msg(CLINIC, name="Nha khoa An Phúc", is_company=True, phone="0281234567"),
        partner_msg(OWNER, name="BS. Trần Minh", parent_id=CLINIC, dental_role="clinic_owner",
                    email="minh@anphuc.vn", phone="0901111111"),
        partner_msg(STAFF, name="Lê Thu", parent_id=CLINIC, dental_role="assistant",
                    email="thu@anphuc.vn", phone="0902222222"),
    ])


def enroll(pid, course_id, name, tags):
    return msg(subject={"model": "res.partner", "id": pid},
               properties={"course_id": course_id, "course_name": name, "course_tags": tags})


def test_idempotent_ingestion(session, tenant):
    m = partner_msg(OWNER, name="A")
    r1 = ingest_odoo_batch(session, tenant, [m])
    r2 = ingest_odoo_batch(session, tenant, [m])
    assert r1["accepted"] == [m["event_id"]]
    assert r2["duplicates"] == [m["event_id"]]
    bad = dict(m, event_id="x", spec_version="2.0")
    assert ingest_odoo_batch(session, tenant, [bad])["rejected"][0]["error"].startswith("unsupported")


def test_clinic_owner_segment_from_staff_learning(session, tenant):
    seed_clinic(session, tenant)
    owner = find_by_partner(session, tenant.id, OWNER)
    assert "clinic_owners" in owner.segment_keys
    assert "owner_staff_basic_no_advanced" not in owner.segment_keys

    ingest_odoo_batch(session, tenant, [enroll(STAFF, 1, "Implant Cơ bản", ["Cơ bản", "Implant"])])
    owner = find_by_partner(session, tenant.id, OWNER)
    assert owner.traits["clinic"]["learners_count"] == 1
    assert "owner_staff_basic_no_advanced" in owner.segment_keys
    trig = session.scalars(select(Activation).where(Activation.kind == "trigger")).all()
    assert [(a.segment_key, a.partner_id) for a in trig] == [("owner_staff_basic_no_advanced", OWNER)]
    # The staff member is not an owner, so she is not targeted.
    staff = find_by_partner(session, tenant.id, STAFF)
    assert "owner_staff_basic_no_advanced" not in staff.segment_keys

    ingest_odoo_batch(session, tenant, [enroll(STAFF, 2, "Implant Nâng cao", ["Nâng cao", "Implant"])])
    owner = find_by_partner(session, tenant.id, OWNER)
    assert "owner_staff_basic_no_advanced" not in owner.segment_keys


def test_shared_phone_is_not_merged(session, tenant):
    seed_clinic(session, tenant)
    # A second partner with the owner's phone (receptionist using clinic owner's number).
    ingest_odoo_batch(session, tenant, [partner_msg(13, name="Lễ tân", parent_id=CLINIC, phone="0901 111 111")])
    assert find_by_partner(session, tenant.id, 13).id != find_by_partner(session, tenant.id, OWNER).id
    sugg = session.scalars(select(MergeSuggestion)).all()
    assert [(s.partner_a, s.partner_b, s.reason) for s in sugg] == [(OWNER, 13, "phone")]


def test_anonymous_web_history_merges_on_login(session, tenant):
    seed_clinic(session, tenant)
    anon = "anon-1"
    views = [{"type": "track", "event": "Course Viewed", "anonymousId": anon, "messageId": f"m{i}",
              "properties": {"course_id": 7, "course_name": "Chỉnh nha"}} for i in range(3)]
    ingest_web_batch(session, tenant, [{"type": "page", "anonymousId": anon, "messageId": "p0",
                                        "properties": {"utm_source": "facebook", "utm_campaign": "cn10"}}] + views)
    anon_profile = session.scalars(select(Profile).where(Profile.partner_id.is_(None), Profile.kind == "person")).one()
    assert anon_profile.traits["hot_courses"][0]["views"] == 3
    assert "hot_leads" in anon_profile.segment_keys
    assert not session.scalars(select(Activation)).all()  # anonymous → nothing to push

    token = security.make_user_token(SECRET, OWNER)
    res = ingest_web_batch(session, tenant, [{"type": "identify", "anonymousId": anon, "messageId": "i1",
                                              "userToken": token}])
    assert res["accepted"] == 1
    owner = find_by_partner(session, tenant.id, OWNER)
    assert session.get(Profile, anon_profile.id).merged_into == owner.id
    assert owner.traits["first_touch"]["source"] == "facebook"
    assert "hot_leads" in owner.segment_keys
    assert any(a.segment_key == "hot_leads" for a in session.scalars(select(Activation)).all())

    forged = ingest_web_batch(session, tenant, [{"type": "identify", "anonymousId": "x", "messageId": "i2",
                                                 "userToken": token[:-2] + "00"}])
    assert forged["rejected"]


def test_abandoned_cart_and_payment(session, tenant):
    seed_clinic(session, tenant)
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    subject = {"model": "res.partner", "id": OWNER}
    ingest_odoo_batch(session, tenant, [msg(event_type="cart.updated", subject=subject, occurred_at=old,
                                            properties={"order_id": 99, "amount": 4500000})])
    refresh_tenant(session, tenant)
    owner = find_by_partner(session, tenant.id, OWNER)
    assert "checkout_abandoned" in owner.segment_keys

    ingest_odoo_batch(session, tenant, [msg(event_type="order.placed", subject=subject,
                                            properties={"order_id": 99, "amount": 4500000, "provider": "sepay"})])
    owner = find_by_partner(session, tenant.id, OWNER)
    assert owner.traits["open_cart"] is None
    assert owner.traits["awaiting_payment"][0]["provider"] == "sepay"

    ingest_odoo_batch(session, tenant, [msg(event_type="order.paid", subject=subject,
                                            properties={"order_id": 99, "amount": 4500000, "provider": "sepay"})])
    # Odoo confirms website orders after payment: must not flip back to "awaiting".
    ingest_odoo_batch(session, tenant, [msg(event_type="order.confirmed", subject=subject,
                                            properties={"order_id": 99, "amount": 4500000})])
    owner = find_by_partner(session, tenant.id, OWNER)
    assert owner.traits["awaiting_payment"] == []
    assert "checkout_abandoned" not in owner.segment_keys
    assert owner.traits["total_spent"] == 4500000
    assert owner.traits["rfm_segment"] == "new_customer"
    assert any("Thanh toán" in x["label"] and "SePay" in x["label"] for x in owner.traits["timeline"])
