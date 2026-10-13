import json
from urllib.parse import parse_qs, urlsplit

import httpx

from cdp_core import jobs
from cdp_core.models import Entity
from cdp_core.pipeline import ingest_odoo_batch

from .conftest import partner_msg


def test_reconcile_requests_resync_of_stale_and_missing(session, tenant):
    tenant.odoo_url = "http://odoo.test"
    m1 = partner_msg(1, name="A")
    m1["properties"]["write_date"] = "2026-10-01T00:00:00Z"
    m3 = partner_msg(3, name="Gone")
    m3["properties"]["write_date"] = "2026-10-01T00:00:00Z"
    ingest_odoo_batch(session, tenant, [m1, m3])
    session.commit()
    calls = []

    def odoo(request: httpx.Request):
        calls.append(request)
        q = parse_qs(urlsplit(str(request.url)).query)
        if request.url.path == "/cdp/v1/reconcile":
            if q["model"] == ["res.partner"]:
                # 1 changed after our copy, 2 never reached CDP, 3 deleted in Odoo.
                return httpx.Response(200, json={"records": [[1, "2026-10-05T00:00:00Z"], [2, "2026-10-01T00:00:00Z"]]})
            return httpx.Response(400, json={"error": "model not mapped"})
        return httpx.Response(200, json={"queued": 2})

    result = jobs.reconcile("dmag", httpx.Client(transport=httpx.MockTransport(odoo)))
    assert result == {"res.partner": {"odoo": 2, "resync": 2, "deleted": 1}}
    resync = [c for c in calls if c.url.path == "/cdp/v1/resync"]
    assert json.loads(resync[0].content) == {"model": "res.partner", "ids": [1, 2]}
    assert all("X-CDP-Signature" in c.headers for c in calls)
    session.expire_all()
    assert session.get(Entity, ("dmag", "res.partner", 3)).deleted
