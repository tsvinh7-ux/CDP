import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone

import httpx
from fastapi.testclient import TestClient

from cdp_core import app as app_module, dashboard, destinations, predict
from cdp_core.app import app
from cdp_core.identity import find_by_partner
from cdp_core.models import Delivery, Tenant
from cdp_core.pipeline import ingest_odoo_batch, ingest_web_batch, ingest_zalo
from cdp_core.simulate import simulate
from cdp_core.touchpoints import for_profile

from .conftest import SECRET, msg, partner_msg

H = {"X-Admin-Token": "change-me"}


def test_markov_backoff_and_delays():
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    seq = ["Course Viewed", "cart.updated", "order.placed", "order.paid"]
    seqs = [(seq, [t0 + timedelta(days=i) for i in range(4)]) for _ in range(5)]
    model = predict._markov_fit(seqs)
    nxt = predict.next_touchpoints(model, ["Course Viewed", "cart.updated"])
    assert nxt[0]["key"] == "order.placed" and nxt[0]["probability"] > 0.8 and nxt[0]["expected_in_days"] == 1
    assert nxt[0]["channel"] == "commerce" and nxt[0]["label"] == "Đặt đơn"
    # Unseen context backs off to the global distribution instead of failing.
    assert predict.next_touchpoints(model, ["zalo.follow"])


def test_logistic_regression_learns_and_auc():
    X = [[i / 100, (i % 7) / 7] for i in range(200)]
    y = [1 if x[0] > 0.6 else 0 for x in X]
    w = predict._fit_logistic(X, y)
    scores = [predict._sigmoid(w[0] + w[1] * x[0] + w[2] * x[1]) for x in X]
    assert predict._auc(scores, y) > 0.99
    assert predict._auc([0.1, 0.2], [0, 0]) is None


def test_simulation_trains_models_better_than_baselines(session):
    session.commit()
    stats = simulate("sim_t", people=400, days=120, seed=3)
    nt, pp = stats["models"]["next_touchpoint"], stats["models"]["product_propensity"]
    assert nt["accuracy_at_3"] > nt["baseline_accuracy_at_3"]
    assert pp["mode"] == "logistic_regression" and pp["positives"] >= 15
    session.expire_all()
    tenant = session.get(Tenant, "sim_t")
    board = dashboard.build(session, tenant, 30)
    assert board["tenant"]["simulated"] and board["kpis"]["revenue"] > 0
    funnel = [f["people"] for f in board["funnel"]]
    assert funnel[0] >= funnel[-1] > 0
    assert board["product_forecast"] and board["models"]["product_propensity"]["metrics"]["auc"] is not None
    assert len(board["channel_weeks"]) >= 4 and board["touchpoints_by_channel"][0]["channel"] == "web"


def test_journey_endpoint_and_predictions(session, tenant):
    ingest_odoo_batch(session, tenant, [
        partner_msg(11, name="BS. Minh", email="minh@x.vn"),
        msg(event_type="lead.created", subject={"model": "res.partner", "id": 11}, properties={"source": "facebook"}),
        msg(event_type="event.registered", subject={"model": "res.partner", "id": 11},
            properties={"event_name": "Webinar Implant"}),
        msg(event_type="marketing.email_opened", subject={"model": "res.partner", "id": 11},
            properties={"campaign": "Ưu đãi"}),
    ])
    ingest_web_batch(session, tenant, [{"type": "page", "anonymousId": "a", "messageId": "p",
                                        "properties": {"utm_source": "facebook", "utm_campaign": "implant"}},
                                       {"type": "identify", "anonymousId": "a", "messageId": "i",
                                        "userToken": __import__("cdp_core.security", fromlist=["x"]).make_user_token(SECRET, 11)}])
    predict.train(session, tenant)
    session.commit()
    owner = find_by_partner(session, tenant.id, 11)
    client = TestClient(app)
    j = client.get(f"/v1/admin/profiles/{owner.id}/journey", headers=H).json()
    channels = {x["channel"] for x in j["touchpoints"]}
    assert {"facebook", "sales", "event", "email", "web"} <= channels
    assert j["stage_reached"] == "consideration"
    assert [s["key"] for s in j["meta"]["stages"]][0] == "awareness"
    # Signed embed for Odoo.
    exp = int(time.time()) + 600
    sig = hmac.new(SECRET.encode(), f"dmag.11.{exp}".encode(), hashlib.sha256).hexdigest()
    ok = client.get(f"/embed/journey/data?tenant=dmag&partner_id=11&exp={exp}&sig={sig}")
    assert ok.status_code == 200 and ok.json()["profile"]["name"] == "BS. Minh"
    assert client.get(f"/embed/journey/data?tenant=dmag&partner_id=12&exp={exp}&sig={sig}").status_code == 401
    assert client.get("/embed/journey").status_code == 200


def test_zns_receipts_become_touchpoints(session, tenant):
    ingest_odoo_batch(session, tenant, [partner_msg(11, name="A", phone="0901111111")])
    owner = find_by_partner(session, tenant.id, 11)
    d = Delivery(tenant_id="dmag", destination_id=1, kind="zns", profile_id=owner.id, status="sent",
                 payload={"segment_key": "checkout_abandoned"})
    session.add(d)
    session.flush()
    res = ingest_zalo(session, tenant, "OA", {"event_name": "user_seen_message", "timestamp": "1760343330000",
                                              "message": {"tracking_id": f"cdp-{d.id}", "msg_id": "m1"}})
    assert res["accepted"]
    assert "seen_at" in d.payload
    keys = [t.key for t in for_profile(session, tenant.id, owner.id)]
    assert "zalo.user_seen_message" in keys and "zalo.zns_sent" in keys


def test_facebook_lead_ads_webhook(session, tenant, monkeypatch):
    destinations.create(session, "dmag", "facebook_leads", "Lead Ads", {"verify_token": "vt"},
                        {"app_secret": "as", "page_access_token": "pat"})
    session.commit()

    def graph(request: httpx.Request):
        assert request.url.params["access_token"] == "pat"
        return httpx.Response(200, json={"id": "L1", "created_time": "2026-10-13T08:00:00+0000",
                                         "campaign_name": "Implant T10", "field_data": [
                                             {"name": "full_name", "values": ["Phạm Lan"]},
                                             {"name": "email", "values": ["lan@x.vn"]},
                                             {"name": "phone_number", "values": ["+84 912 555 555"]}]})

    monkeypatch.setattr(app_module, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(graph)))
    client = TestClient(app)
    assert client.get("/v1/webhooks/facebook/dmag?hub.mode=subscribe&hub.verify_token=vt&hub.challenge=42").text == "42"
    assert client.get("/v1/webhooks/facebook/dmag?hub.mode=subscribe&hub.verify_token=no&hub.challenge=1").status_code == 403
    body = json.dumps({"entry": [{"changes": [{"field": "leadgen", "value": {"leadgen_id": "L1", "form_id": "F"}}]}]}).encode()
    sig = "sha256=" + hmac.new(b"as", body, hashlib.sha256).hexdigest()
    assert client.post("/v1/webhooks/facebook/dmag", content=body, headers={"X-Hub-Signature-256": "sha256=x"}).status_code == 401
    res = client.post("/v1/webhooks/facebook/dmag", content=body, headers={"X-Hub-Signature-256": sig}).json()
    assert res["results"][0]["accepted"]
    # A later Odoo partner with the same e-mail inherits the lead's history.
    session.expire_all()
    ingest_odoo_batch(session, tenant, [partner_msg(20, name="Phạm Lan", email="lan@x.vn")])
    lan = find_by_partner(session, tenant.id, 20)
    assert lan.traits["first_touch"]["medium"] == "lead_ads"
