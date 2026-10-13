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
