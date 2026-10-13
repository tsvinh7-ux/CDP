"""Analytics: event export to ClickHouse and revenue attribution.

PostgreSQL keeps what identity/traits need; ClickHouse (CDP_CLICKHOUSE_URL,
e.g. http://user:pass@clickhouse:8123/cdp) receives every event for long-term
storage and fast behavioural queries. The export is incremental (watermark)
and idempotent (ReplacingMergeTree on event_id).
"""

import json
from collections import defaultdict
from urllib.parse import urlsplit

import httpx
from sqlalchemy import BigInteger, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .config import settings
from .db import Base
from .models import Event, Profile, utcnow
from .traits import aware, iso

DDL = """
CREATE TABLE IF NOT EXISTS {db}.cdp_events (
    tenant_id   LowCardinality(String),
    event_id    String,
    source      LowCardinality(String),
    kind        LowCardinality(String),
    event_type  LowCardinality(String),
    occurred_at DateTime64(3, 'UTC'),
    received_at DateTime64(3, 'UTC'),
    profile_id  String,
    properties  String,
    context     String
) ENGINE = ReplacingMergeTree
PARTITION BY toYYYYMM(occurred_at)
ORDER BY (tenant_id, event_type, occurred_at, event_id)
"""


class ExportState(Base):
    __tablename__ = "export_state"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[int] = mapped_column(BigInteger, default=0)


class ClickHouse:
    def __init__(self, url: str, client: httpx.Client | None = None):
        parts = urlsplit(url)
        self.base = f"{parts.scheme}://{parts.hostname}:{parts.port or 8123}/"
        self.db = parts.path.strip("/") or "default"
        self.auth = (parts.username or "default", parts.password or "")
        self.client = client or httpx.Client(timeout=60)

    def execute(self, sql: str, data: bytes | None = None) -> str:
        resp = self.client.post(self.base, params={"query": sql, "database": self.db}, content=data or b"",
                                auth=self.auth)
        if resp.status_code != 200:
            raise RuntimeError(f"ClickHouse error {resp.status_code}: {resp.text[:500]}")
        return resp.text

    def query_json(self, sql: str) -> list[dict]:
        text = self.execute(sql + " FORMAT JSONEachRow")
        return [json.loads(line) for line in text.splitlines() if line.strip()]


def export_events(session: Session, ch: ClickHouse | None = None, batch: int = 10000) -> int:
    """Copy new events (id > watermark) to ClickHouse. Returns how many."""
    if ch is None:
        if not settings.clickhouse_url:
            return 0
        ch = ClickHouse(settings.clickhouse_url)
    ch.execute(DDL.format(db=ch.db))
    state = session.get(ExportState, "clickhouse_events") or ExportState(name="clickhouse_events", value=0)
    session.add(state)
    total = 0
    while True:
        rows = session.scalars(select(Event).where(Event.id > state.value).order_by(Event.id).limit(batch)).all()
        if not rows:
            break
        lines = "\n".join(json.dumps({
            "tenant_id": e.tenant_id, "event_id": e.event_id, "source": e.source, "kind": e.kind,
            "event_type": e.event_type,
            "occurred_at": aware(e.occurred_at).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            "received_at": aware(e.received_at).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            "profile_id": e.profile_id or "",
            "properties": json.dumps((e.payload or {}).get("properties") or {}, ensure_ascii=False),
            "context": json.dumps((e.payload or {}).get("context") or {}, ensure_ascii=False),
        }, ensure_ascii=False) for e in rows)
        ch.execute("INSERT INTO cdp_events FORMAT JSONEachRow", lines.encode())
        state.value = rows[-1].id
        session.flush()
        total += len(rows)
    return total


def attribution(session: Session, tenant_id: str) -> dict:
    """Paid revenue by first-touch source/campaign (who brought the customer)."""
    profiles = {p.id: p for p in session.scalars(select(Profile).where(Profile.tenant_id == tenant_id,
                                                                       Profile.merged_into.is_(None)))}
    rows: dict = defaultdict(lambda: {"revenue": 0.0, "orders": 0, "customers": set()})
    paid = session.scalars(select(Event).where(Event.tenant_id == tenant_id, Event.event_type == "order.paid")).all()
    for ev in paid:
        prof = profiles.get(ev.profile_id)
        touch = ((prof.traits or {}).get("first_touch") if prof else None) or {}
        key = (touch.get("source") or "(không rõ / trực tiếp)", touch.get("medium") or "", touch.get("campaign") or "")
        amount = float(((ev.payload or {}).get("properties") or {}).get("amount") or 0)
        rows[key]["revenue"] += amount
        rows[key]["orders"] += 1
        rows[key]["customers"].add(ev.profile_id)
    out = [{"source": k[0], "medium": k[1], "campaign": k[2], "revenue": v["revenue"], "orders": v["orders"],
            "customers": len(v["customers"])} for k, v in rows.items()]
    out.sort(key=lambda r: -r["revenue"])
    return {"model": "first_touch", "generated_at": iso(utcnow()), "rows": out}
