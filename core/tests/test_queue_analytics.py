import json
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from cdp_core import analytics, queue, security
from cdp_core.app import app
from cdp_core.config import settings
from cdp_core.identity import find_by_partner
from cdp_core.models import Event, IngestQueue
from cdp_core.pipeline import ingest_odoo_batch, ingest_web_batch

from .conftest import SECRET, msg, partner_msg


@pytest.fixture()
def queue_mode(monkeypatch):
    monkeypatch.setattr(settings, "ingest_mode", "queue")
    monkeypatch.setattr(queue, "BACKEND", "postgres")


def test_queue_mode_acknowledges_then_consumer_processes(session, tenant, queue_mode):
    session.commit()
    client = TestClient(app)
    body = json.dumps({"messages": [partner_msg(1, name="A", email="a@x.vn"), {"event_id": "bad"}]}).encode()
    res = client.post("/v1/batch", content=body, headers=security.signed_headers("dmag", SECRET, body))
    assert res.status_code == 200
    assert len(res.json()["accepted"]) == 1 and len(res.json()["rejected"]) == 1
    session.expire_all()
    assert find_by_partner(session, "dmag", 1) is None  # not processed yet
    session.commit()  # release the read transaction (SQLite allows a single writer)
    assert queue.consume_postgres() == 1
    session.expire_all()
    assert find_by_partner(session, "dmag", 1).traits["name"] == "A"
    assert queue.consume_postgres() == 0


def test_poison_request_does_not_block_others(session, tenant, queue_mode, monkeypatch):
    queue.enqueue(session, "dmag", "odoo", [partner_msg(1, name="ok")])
    queue.enqueue(session, "dmag", "odoo", [partner_msg(2, name="poison")])
    session.commit()
    original = queue.pipeline.ingest_odoo_batch

    def flaky(s, t, messages):
        if any((m.get("properties") or {}).get("fields", {}).get("name") == "poison" for m in messages):
            raise ValueError("boom")
        return original(s, t, messages)

    monkeypatch.setattr(queue.pipeline, "ingest_odoo_batch", flaky)
    queue.consume_postgres()
    session.expire_all()
    rows = {r.payload[0]["properties"]["fields"]["name"]: r for r in session.scalars(select(IngestQueue))}
    assert rows["ok"].processed_at is not None
    assert rows["poison"].processed_at is None and rows["poison"].attempts == 1 and "boom" in rows["poison"].error


def test_first_touch_attribution(session, tenant):
    ingest_odoo_batch(session, tenant, [partner_msg(1, name="A")])
    ingest_web_batch(session, tenant, [{"type": "page", "anonymousId": "v", "messageId": "p1",
                                        "properties": {"utm_source": "facebook", "utm_campaign": "implant"}},
                                       {"type": "identify", "anonymousId": "v", "messageId": "i1",
                                        "userToken": security.make_user_token(SECRET, 1)}])
    ingest_odoo_batch(session, tenant, [msg(event_type="order.paid", subject={"model": "res.partner", "id": 1},
                                            properties={"order_id": 1, "amount": 3500000})])
    rows = analytics.attribution(session, "dmag")["rows"]
    assert rows == [{"source": "facebook", "medium": "", "campaign": "implant", "revenue": 3500000.0,
                     "orders": 1, "customers": 1}]


@pytest.mark.skipif(not os.environ.get("CDP_TEST_CLICKHOUSE_URL"), reason="needs ClickHouse")
def test_clickhouse_export_is_incremental(session, tenant):
    ch = analytics.ClickHouse(os.environ["CDP_TEST_CLICKHOUSE_URL"])
    ch.execute("DROP TABLE IF EXISTS cdp_events")
    ingest_odoo_batch(session, tenant, [partner_msg(1, name="A"), msg(event_type="order.paid",
                      subject={"model": "res.partner", "id": 1}, properties={"amount": 5})])
    assert analytics.export_events(session, ch) == 2
    assert analytics.export_events(session, ch) == 0
    rows = ch.query_json("SELECT event_type, JSONExtractFloat(properties, 'amount') AS amount FROM cdp_events "
                         "WHERE tenant_id = 'dmag' ORDER BY event_type")
    assert rows == [{"event_type": "entity.upserted", "amount": 0}, {"event_type": "order.paid", "amount": 5}]


@pytest.mark.skipif(not os.environ.get("CDP_TEST_KAFKA"), reason="needs Kafka/Redpanda")
def test_kafka_backend_roundtrip(session, tenant, monkeypatch):
    monkeypatch.setattr(queue, "KAFKA_TOPIC", f"cdp.test.{uuid.uuid4().hex[:8]}")
    session.commit()
    queue.enqueue(session, "dmag", "web", [{"type": "page", "anonymousId": "k1", "messageId": "kp1"}],
                  backend="kafka")
    processed = 0
    for _ in range(20):
        processed += queue.consume_kafka(timeout=2.0, group=f"g-{uuid.uuid4().hex[:6]}")
        if processed:
            break
    assert processed == 1
    session.expire_all()
    assert session.scalars(select(Event).where(Event.event_id == "kp1")).one()
