import json

from fastapi.testclient import TestClient

from cdp_core import security
from cdp_core.app import app

from .conftest import SECRET, partner_msg


def test_batch_requires_valid_signature(session, tenant):
    session.commit()
    client = TestClient(app)
    body = json.dumps({"messages": [partner_msg(1, name="A")]}).encode()
    headers = security.signed_headers("dmag", SECRET, body)
    assert client.post("/v1/batch", content=body, headers={**headers, "X-CDP-Signature": "sha256=bad"}).status_code == 401
    res = client.post("/v1/batch", content=body, headers=headers)
    assert res.status_code == 200 and len(res.json()["accepted"]) == 1


def test_collect_with_write_key(session, tenant):
    session.commit()
    client = TestClient(app)
    payload = {"writeKey": "wk_test", "batch": [{"type": "page", "anonymousId": "a1", "messageId": "m1"}]}
    res = client.post("/v1/collect", content=json.dumps(payload), headers={"Content-Type": "text/plain"})
    assert res.status_code == 200 and res.json()["accepted"] == 1
    assert client.post("/v1/collect", content=json.dumps({**payload, "writeKey": "nope"})).status_code == 401


def test_zalo_webhook_endpoint_checks_signature(session, tenant):
    import hashlib
    from cdp_core import destinations
    destinations.create(session, "dmag", "zalo_zns", "OA", {"app_id": "APP", "oa_id": "OA1"},
                        {"oa_secret_key": "oas"})
    session.commit()
    client = TestClient(app)
    body = json.dumps({"event_name": "follow", "timestamp": "1760343330000", "follower": {"id": "u1"}}).encode()
    good = "mac=" + hashlib.sha256(b"APP" + body + b"1760343330000" + b"oas").hexdigest()
    assert client.post("/v1/webhooks/zalo/dmag", content=body, headers={"X-ZEvent-Signature": "mac=x"}).status_code == 401
    res = client.post("/v1/webhooks/zalo/dmag", content=body, headers={"X-ZEvent-Signature": good})
    assert res.status_code == 200 and res.json()["accepted"]


def test_destination_secrets_are_masked(session, tenant):
    session.commit()
    client = TestClient(app)
    headers = {"X-Admin-Token": "change-me"}
    res = client.post("/v1/admin/destinations?tenant=dmag", headers=headers,
                      json={"type": "facebook_capi", "name": "Pixel", "config": {"pixel_id": "1"},
                            "secrets": {"access_token": "very-secret"}})
    assert res.status_code == 200
    assert "very-secret" not in res.text and res.json()["config"]["secrets"] == {"access_token": "••••"}
