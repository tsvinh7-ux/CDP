"""Asynchronous ingestion (CDP_INGEST_MODE=queue).

The API only authenticates, validates and stores the request, then answers;
a consumer (`python -m cdp_core.consumer`) does identity/traits/segments.
Backends:
  * postgres (default) – table `ingest_queue`, FOR UPDATE SKIP LOCKED; enough
    for DMAG-scale traffic with no extra infrastructure;
  * kafka   – Redpanda/Kafka topic (CDP_QUEUE_BACKEND=kafka) for higher volume.
Processing is idempotent (event_id), so at-least-once delivery is safe.
"""

import json
import logging
import os
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import pipeline
from .db import session_scope
from .models import IngestQueue, Tenant, utcnow

_logger = logging.getLogger(__name__)
BACKEND = os.environ.get("CDP_QUEUE_BACKEND", "postgres")
KAFKA_BOOTSTRAP = os.environ.get("CDP_KAFKA_BOOTSTRAP", "localhost:9092")
KAFKA_TOPIC = os.environ.get("CDP_KAFKA_TOPIC", "cdp.ingest")
_producer = None


def _kafka_producer():
    global _producer
    if _producer is None:
        from confluent_kafka import Producer

        _producer = Producer({"bootstrap.servers": KAFKA_BOOTSTRAP, "enable.idempotence": True,
                              "acks": "all", "linger.ms": 20})
    return _producer


def enqueue(session: Session, tenant_id: str, source: str, payload: list, backend: str | None = None):
    backend = backend or BACKEND
    if backend == "kafka":
        producer = _kafka_producer()
        # Keyed by tenant: per-tenant ordering within a partition.
        producer.produce(KAFKA_TOPIC, key=tenant_id, value=json.dumps(
            {"tenant_id": tenant_id, "source": source, "payload": payload}, ensure_ascii=False).encode())
        if producer.flush(10) > 0:
            raise RuntimeError("Kafka did not acknowledge the message")
    else:
        session.add(IngestQueue(tenant_id=tenant_id, source=source, payload=payload))
        session.flush()


def process(session: Session, items: list[tuple[str, str, list]]) -> dict:
    """Process queued requests, merged per (tenant, source) so each profile is refreshed once."""
    grouped: dict[tuple[str, str], list] = defaultdict(list)
    for tenant_id, source, payload in items:
        grouped[(tenant_id, source)].extend(payload)
    stats = {}
    for (tenant_id, source), payload in grouped.items():
        tenant = session.get(Tenant, tenant_id)
        if tenant is None:
            continue
        if source == "odoo":
            res = pipeline.ingest_odoo_batch(session, tenant, payload)
            n = len(res["accepted"])
        elif source == "web":
            n = pipeline.ingest_web_batch(session, tenant, payload)["accepted"]
        else:
            n = 0
        stats[f"{tenant_id}/{source}"] = n
    return stats


def consume_postgres(limit: int = 50) -> int:
    """Process up to `limit` queued requests in one transaction. Returns how many."""
    with session_scope() as session:
        rows = session.scalars(select(IngestQueue).where(IngestQueue.processed_at.is_(None),
                                                         IngestQueue.attempts < 5)
                               .order_by(IngestQueue.id).limit(limit).with_for_update(skip_locked=True)).all()
        if not rows:
            return 0
        now = utcnow()
        try:
            with session.begin_nested():
                process(session, [(r.tenant_id, r.source, r.payload) for r in rows])
            for r in rows:
                r.processed_at = now
        except Exception:
            # A poison request must not block the others: retry them one by one.
            _logger.exception("Queue batch failed, isolating requests")
            for r in rows:
                try:
                    with session.begin_nested():
                        process(session, [(r.tenant_id, r.source, r.payload)])
                    r.processed_at = now
                except Exception as exc:
                    r.attempts += 1
                    r.error = str(exc)[:1000]
        return len(rows)


def consume_kafka(max_messages: int = 500, timeout: float = 1.0, group: str = "cdp-core") -> int:
    from confluent_kafka import Consumer

    consumer = Consumer({"bootstrap.servers": KAFKA_BOOTSTRAP, "group.id": group,
                         "enable.auto.commit": False, "auto.offset.reset": "earliest"})
    consumer.subscribe([KAFKA_TOPIC])
    try:
        msgs = [m for m in consumer.consume(num_messages=max_messages, timeout=timeout) if not m.error()]
        if not msgs:
            return 0
        items = []
        for m in msgs:
            doc = json.loads(m.value())
            items.append((doc["tenant_id"], doc["source"], doc["payload"]))
        with session_scope() as session:
            process(session, items)
        consumer.commit(asynchronous=False)  # only after the database commit
        return len(msgs)
    finally:
        consumer.close()
