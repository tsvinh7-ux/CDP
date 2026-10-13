"""Destinations against mocked Facebook/Zalo APIs (httpx.MockTransport)."""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs

import httpx
from sqlalchemy import select

from cdp_core import destinations, privacy
from cdp_core.destinations.zalo import verify_webhook
from cdp_core.identity import find_by_partner
from cdp_core.jobs import refresh_tenant
from cdp_core.models import AudienceMember, Delivery, Identity
from cdp_core.pipeline import ingest_odoo_batch, ingest_zalo

from .conftest import msg, partner_msg

OWNER, OTHER = 11, 12


def sha(v):
    return hashlib.sha256(v.encode()).hexdigest()


class FakeApi:
    def __init__(self):
        self.calls = []
        self.zns_counter = 0

    def handler(self, request: httpx.Request):
        body = request.content.decode()
        self.calls.append((request.method, request.url.host, request.url.path, body, dict(request.headers)))
        if request.url.host == "graph.facebook.com":
            if request.url.path.endswith("/customaudiences"):
                return httpx.Response(200, json={"id": "aud_1"})
            if request.url.path.endswith("/events"):
                return httpx.Response(200, json={"events_received": 1, "fbtrace_id": "trace"})
            return httpx.Response(200, json={"num_received": 1})
        if request.url.host == "oauth.zaloapp.com":
            return httpx.Response(200, json={"access_token": "AT", "refresh_token": "RT2", "expires_in": "90000"})
        if request.url.host == "business.openapi.zalo.me":
            self.zns_counter += 1
            return httpx.Response(200, json={"error": 0, "message": "Success", "data": {"msg_id": f"m{self.zns_counter}"}})
        return httpx.Response(404)

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self.handler))

    def find(self, path_part):
        return [c for c in self.calls if path_part in c[2]]


def consent(pid, purpose, granted=True):
    return msg(event_type="consent.updated", subject={"model": "res.partner", "id": pid},
               properties={"purpose": purpose, "granted": granted})


def seed(session, tenant):
    ingest_odoo_batch(session, tenant, [
        partner_msg(OWNER, name="BS. Minh", email="Minh@X.vn", phone="0901111111", dental_role="clinic_owner"),
        partner_msg(OTHER, name="Lê Thu", email="thu@x.vn", phone="0902222222"),
        consent(OWNER, "ads"), consent(OWNER, "marketing"),
    ])


def test_capi_sends_hashed_purchase_only_with_ads_consent(session, tenant):
    seed(session, tenant)
    dest = destinations.create(session, tenant.id, "facebook_capi", "Pixel", {"pixel_id": "PX1"},
                               {"access_token": "tok"})
    ingest_odoo_batch(session, tenant, [
        msg(event_type="order.paid", subject={"model": "res.partner", "id": OWNER},
            properties={"order_id": 5, "amount": 3500000, "items": [{"product_id": 7}], "website_id": 1}),
        msg(event_type="order.paid", subject={"model": "res.partner", "id": OTHER},
            properties={"order_id": 6, "amount": 100}),
    ])
    api = FakeApi()
    result = destinations.run(session, tenant, api.client())
    assert result[f"facebook_capi:{dest.id}"] == {"collected": 2, "sent": 1, "skipped": 1}
    (method, host, path, body, _h), = api.find("/PX1/events")
    form = parse_qs(body)
    assert form["access_token"] == ["tok"]
    event, = json.loads(form["data"][0])
    assert event["event_name"] == "Purchase" and event["custom_data"]["value"] == 3500000.0
    assert event["user_data"]["em"] == [sha("minh@x.vn")]
    assert event["user_data"]["ph"] == [sha("84901111111")]
    assert event["action_source"] == "website"
    skipped = session.scalars(select(Delivery).where(Delivery.status == "skipped")).one()
    assert skipped.reason == "no ads consent"
    # Watermark: nothing collected twice.
    assert destinations.run(session, tenant, api.client())[f"facebook_capi:{dest.id}"]["collected"] == 0


def test_custom_audience_add_and_remove_deltas(session, tenant):
    seed(session, tenant)
    destinations.create(session, tenant.id, "facebook_audience", "Ads", {
        "ad_account_id": "act_9", "segments": ["clinic_owners"]}, {"access_token": "tok"})
    api = FakeApi()
    destinations.run(session, tenant, api.client())
    assert len(api.find("/act_9/customaudiences")) == 1
    (method, _h, _p, body, _hd), = api.find("/aud_1/users")
    payload = json.loads(parse_qs(body)["payload"][0])
    assert method == "POST" and payload["data"] == [[sha("minh@x.vn"), sha("84901111111")]]

    api2 = FakeApi()
    destinations.run(session, tenant, api2.client())
    assert api2.calls == []  # nothing changed → no API call

    # Withdrawing ads consent removes the person from the audience.
    ingest_odoo_batch(session, tenant, [consent(OWNER, "ads", False)])
    api3 = FakeApi()
    destinations.run(session, tenant, api3.client())
    (method, *_rest), = api3.find("/aud_1/users")
    assert method == "DELETE"
    assert session.scalars(select(AudienceMember)).all() == []


def test_erasure_queues_audience_removal(session, tenant):
    seed(session, tenant)
    destinations.create(session, tenant.id, "facebook_audience", "Ads", {
        "ad_account_id": "act_9", "segments": ["clinic_owners"]}, {"access_token": "tok"})
    destinations.run(session, tenant, FakeApi().client())
    owner = find_by_partner(session, tenant.id, OWNER)
    privacy.erase(session, tenant, owner)
    api = FakeApi()
    destinations.run(session, tenant, api.client())
    deletes = [c for c in api.find("/aud_1/users") if c[0] == "DELETE"]
    assert deletes and sha("minh@x.vn") in deletes[0][3]


def test_zns_on_segment_entry_with_consent_and_frequency_cap(session, tenant):
    seed(session, tenant)
    destinations.create(session, tenant.id, "zalo_zns", "Zalo OA", {
        "app_id": "APP", "templates": {
            "checkout_abandoned": {"template_id": "T1", "purpose": "marketing",
                                   "data": {"name": "name", "amount": "open_cart.amount", "brand": "=DMAG"}},
        }}, {"secret_key": "sk", "refresh_token": "RT1"})
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    ingest_odoo_batch(session, tenant, [
        msg(event_type="cart.updated", subject={"model": "res.partner", "id": pid}, occurred_at=old,
            properties={"order_id": pid, "amount": 4500000}) for pid in (OWNER, OTHER)])
    refresh_tenant(session, tenant)
    api = FakeApi()
    result = destinations.run(session, tenant, api.client())
    stats = next(v for k, v in result.items() if k.startswith("zalo_zns"))
    # OWNER consented to marketing; OTHER did not (opt-in policy).
    assert stats == {"collected": 2, "sent": 1, "skipped": 1, "failed": 0}
    (token_call,) = api.find("/v4/oa/access_token")
    assert parse_qs(token_call[3])["refresh_token"] == ["RT1"]
    (send,) = api.find("/message/template")
    body = json.loads(send[3])
    assert body["phone"] == "84901111111" and body["template_id"] == "T1"
    assert body["template_data"] == {"name": "BS. Minh", "amount": 4500000, "brand": "DMAG"}
    assert send[4]["access_token"] == "AT"

    # Same day, another marketing trigger: blocked by the default cap (1/day).
    dest_state_rt = session.scalars(select(Delivery).where(Delivery.kind == "zns", Delivery.status == "sent")).one()
    assert dest_state_rt.external_id == "m1"
    ingest_odoo_batch(session, tenant, [msg(event_type="order.paid", subject={"model": "res.partner", "id": OWNER},
                                            properties={"order_id": OWNER, "amount": 4500000})])
    ingest_odoo_batch(session, tenant, [msg(event_type="cart.updated", subject={"model": "res.partner", "id": OWNER},
                                            occurred_at=old, properties={"order_id": 99, "amount": 1})])
    refresh_tenant(session, tenant)
    api2 = FakeApi()
    stats2 = next(v for k, v in destinations.run(session, tenant, api2.client()).items() if k.startswith("zalo"))
    assert stats2["sent"] == 0 and stats2["skipped"] == 1
    assert "frequency cap" in session.scalars(select(Delivery).where(Delivery.kind == "zns")
                                              .order_by(Delivery.id.desc())).first().reason


def test_zalo_webhook_links_zalo_user_to_partner(session, tenant):
    seed(session, tenant)
    body = {"app_id": "APP", "event_name": "user_submit_info", "timestamp": "1760343330000",
            "sender": {"id": "zuid-1"}, "info": {"name": "Minh", "phone": "0901111111"}}
    raw = json.dumps(body).encode()
    sig = "mac=" + hashlib.sha256(b"APP" + raw + b"1760343330000" + b"oasecret").hexdigest()
    assert verify_webhook("APP", "oasecret", raw, "1760343330000", sig)
    assert not verify_webhook("APP", "wrong", raw, "1760343330000", sig)
    ingest_zalo(session, tenant, "OA1", {**body, "event_name": "follow", "follower": {"id": "zuid-1"}})
    ingest_zalo(session, tenant, "OA1", body)
    owner = find_by_partner(session, tenant.id, OWNER)
    kinds = {i.kind for i in session.scalars(select(Identity).where(Identity.profile_id == owner.id))}
    assert "zalo" in kinds
    assert owner.traits["zalo_follower"] is True


def test_audience_ids_persist_across_runs(session, tenant):
    """Regression: the 2nd audience id was lost (in-place dict mutation not detected), recreated each run."""
    seed(session, tenant)
    ingest_odoo_batch(session, tenant, [msg(event_type="order.paid", subject={"model": "res.partner", "id": OWNER},
                                            properties={"order_id": 1, "amount": 20000000})])
    destinations.create(session, tenant.id, "facebook_audience", "Ads", {
        "ad_account_id": "act_9", "segments": ["clinic_owners", "high_value"]}, {"access_token": "tok"})
    destinations.run(session, tenant, FakeApi().client())
    session.commit()
    session.expire_all()
    api = FakeApi()
    destinations.run(session, tenant, api.client())
    assert api.find("/customaudiences") == []
