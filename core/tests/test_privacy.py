from sqlalchemy import select

from cdp_core import consent, privacy
from cdp_core.identity import find_by_partner
from cdp_core.models import Event, Identity, PrivacyRequest
from cdp_core.pipeline import ingest_odoo_batch, ingest_web_batch

from .conftest import msg, partner_msg

OWNER = 11


def consent_msg(purpose, granted):
    return msg(event_type="consent.updated", subject={"model": "res.partner", "id": OWNER},
               properties={"purpose": purpose, "granted": granted, "channel": "form", "evidence_ref": "F-1"})


def test_default_policy_is_opt_in_for_marketing_and_ads(session, tenant):
    ingest_odoo_batch(session, tenant, [partner_msg(OWNER, name="A", email="a@x.vn")])
    owner = find_by_partner(session, tenant.id, OWNER)
    assert owner.traits["consent"] == {"care": True, "marketing": False, "ads": False, "analytics": True}

    ingest_odoo_batch(session, tenant, [consent_msg("ads", True), consent_msg("marketing", True)])
    owner = find_by_partner(session, tenant.id, OWNER)
    assert consent.allowed(tenant, owner.traits, "ads")
    assert consent.allowed(tenant, owner.traits, "marketing")

    ingest_odoo_batch(session, tenant, [consent_msg("marketing", False)])
    assert not consent.allowed(tenant, find_by_partner(session, tenant.id, OWNER).traits, "marketing")


def test_web_consent_follows_merge(session, tenant):
    ingest_odoo_batch(session, tenant, [partner_msg(OWNER, name="A")])
    ingest_web_batch(session, tenant, [{"type": "track", "event": "Consent Updated", "anonymousId": "v1",
                                        "messageId": "c1", "properties": {"purposes": {"ads": True}}}])
    from cdp_core.security import make_user_token
    ingest_web_batch(session, tenant, [{"type": "identify", "anonymousId": "v1", "messageId": "i1",
                                        "userToken": make_user_token("test-secret", OWNER)}])
    assert find_by_partner(session, tenant.id, OWNER).traits["consent"]["ads"] is True


def test_erasure_removes_personal_data_and_blocks_new_data(session, tenant):
    ingest_odoo_batch(session, tenant, [
        partner_msg(OWNER, name="BS. Minh", email="minh@x.vn", phone="0901111111"),
        msg(event_type="course.enrolled", subject={"model": "res.partner", "id": OWNER},
            properties={"course_id": 1, "course_name": "Implant"}),
    ])
    owner = find_by_partner(session, tenant.id, OWNER)
    exported = privacy.export(session, tenant, owner)
    assert exported["events"] and {"kind": "email", "value": "minh@x.vn"} in exported["identities"]

    ingest_odoo_batch(session, tenant, [msg(event_type="privacy.erasure_requested",
                                            subject={"model": "res.partner", "id": OWNER})])
    owner = find_by_partner(session, tenant.id, OWNER)
    assert owner.traits.get("erased") and owner.segment_keys == [] and owner.dirty
    assert session.scalars(select(Event).where(Event.profile_id == owner.id)).all() == []
    assert [i.kind for i in session.scalars(select(Identity).where(Identity.profile_id == owner.id))] == ["partner"]
    assert session.scalars(select(PrivacyRequest).where(PrivacyRequest.kind == "erase")).one()

    # Odoo keeps sending (until the partner is excluded there): nothing is stored again.
    ingest_odoo_batch(session, tenant, [
        partner_msg(OWNER, seq=5, name="BS. Minh", email="minh@x.vn"),
        msg(event_type="order.paid", subject={"model": "res.partner", "id": OWNER}, properties={"amount": 1}),
    ])
    owner = find_by_partner(session, tenant.id, OWNER)
    assert owner.traits.get("erased")
    assert session.scalars(select(Event).where(Event.profile_id == owner.id)).all() == []
