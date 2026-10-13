"""Periodic jobs: time-based trait/segment refresh, counts, push to Odoo."""

import json
import logging
from datetime import timedelta

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import activation, analytics, destinations, segments as seg
from .db import session_scope
from . import security
from .models import Entity, Profile, Segment, SegmentMember, Tenant, utcnow
from .traits import parse_dt
from .pipeline import refresh_profile

_logger = logging.getLogger(__name__)


def create_tenant(session: Session, tenant_id: str, name: str, secret: str, write_key: str,
                  odoo_url: str | None = None, settings: dict | None = None) -> Tenant:
    tenant = Tenant(id=tenant_id, name=name, secret=secret, write_key=write_key,
                    odoo_url=odoo_url, settings=settings or {})
    session.add(tenant)
    cfg = settings or {}
    for s in seg.default_segments(int(cfg.get("abandon_minutes", 60)), int(cfg.get("transfer_minutes", 30)),
                                  cfg.get("vertical", "education")):
        session.add(Segment(tenant_id=tenant_id, key=s["key"], name=s["name"],
                            description=s.get("description"), rule=s["rule"],
                            trigger_on_enter=s.get("trigger_on_enter", False)))
    session.flush()
    return tenant


def refresh_tenant(session: Session, tenant: Tenant):
    """Recompute everything that depends on the clock (abandonment, stalls, recency)."""
    now = utcnow()
    persons = session.scalars(
        select(Profile).where(Profile.tenant_id == tenant.id, Profile.kind == "person",
                              Profile.merged_into.is_(None))
    ).all()
    for p in persons:
        refresh_profile(session, tenant, p, now, cascade=False)
    accounts = session.scalars(
        select(Profile).where(Profile.tenant_id == tenant.id, Profile.kind == "account",
                              Profile.merged_into.is_(None))
    ).all()
    for a in accounts:
        refresh_profile(session, tenant, a, now)
    counts = dict(session.execute(
        select(SegmentMember.segment_key, func.count()).where(SegmentMember.tenant_id == tenant.id)
        .group_by(SegmentMember.segment_key)
    ).all())
    for s in session.scalars(select(Segment).where(Segment.tenant_id == tenant.id)).all():
        s.member_count = counts.get(s.key, 0)


def run_once(tenant_id: str | None = None, push: bool = True, client: httpx.Client | None = None) -> dict:
    results, due_reconcile = {}, {}
    own_client = client is None
    client = client or httpx.Client(timeout=30)
    try:
        with session_scope() as session:
            query = select(Tenant)
            if tenant_id:
                query = query.where(Tenant.id == tenant_id)
            for tenant in session.scalars(query).all():
                last = parse_dt((tenant.settings or {}).get("last_reconcile_at"))
                if tenant.odoo_url and (last is None or utcnow() - last > timedelta(hours=24)):
                    due_reconcile[tenant.id] = last
                refresh_tenant(session, tenant)
                session.flush()
                results[tenant.id] = activation.push_tenant(session, tenant, client) if push else {}
                if push:
                    results[tenant.id]["destinations"] = destinations.run(session, tenant, client)
        if push:
            for tid, last in due_reconcile.items():
                try:
                    results.setdefault(tid, {})["reconcile"] = reconcile(tid, client)
                except httpx.HTTPError as exc:
                    _logger.warning("Reconciliation of %s failed: %s", tid, exc)
            with session_scope() as session:
                try:
                    results["clickhouse_exported"] = analytics.export_events(session)
                except (httpx.HTTPError, RuntimeError) as exc:
                    _logger.warning("ClickHouse export failed: %s", exc)
                    results["clickhouse_error"] = str(exc)
    finally:
        if own_client:
            client.close()
    return results


RECONCILE_MODELS = ("res.partner", "slide.channel")


def reconcile(tenant_id: str, client: httpx.Client | None = None, models=RECONCILE_MODELS) -> dict:
    """Nightly safety net (data contract §4.3): compare (id, write_date) with Odoo and ask for re-sync.

    Catches changes that bypassed the ORM (SQL scripts, imports) or messages lost before the outbox.
    """
    own = client is None
    client = client or httpx.Client(timeout=60)
    out = {}
    try:
        with session_scope() as session:
            tenant = session.get(Tenant, tenant_id)
            if tenant is None or not tenant.odoo_url:
                return {"error": "tenant has no Odoo URL"}
            base = tenant.odoo_url.rstrip("/")
            for model in models:
                odoo_rows, after, mapped = [], 0, True
                while True:
                    path = f"/cdp/v1/reconcile?model={model}&after_id={after}&limit=5000"
                    headers = security.signed_headers(tenant.id, tenant.secret, b"")
                    resp = client.get(base + path, headers=headers)
                    if resp.status_code == 400:
                        mapped = False  # model not mapped in this Odoo
                        break
                    resp.raise_for_status()
                    chunk = resp.json()["records"]
                    odoo_rows += chunk
                    if len(chunk) < 5000:
                        break
                    after = chunk[-1][0]
                if not mapped:
                    continue
                ours = {e.res_id: e for e in session.scalars(select(Entity).where(
                    Entity.tenant_id == tenant.id, Entity.model == model))}
                stale = []
                for rid, write_date in odoo_rows:
                    ent = ours.get(rid)
                    theirs = parse_dt(write_date)
                    mine = parse_dt((ent.fields or {}).get("_write_date")) if ent else None
                    if ent is None or ent.deleted or (theirs and (mine is None or theirs > mine)):
                        stale.append(rid)
                missing_in_odoo = set(ours) - {rid for rid, _w in odoo_rows}
                for rid in missing_in_odoo:
                    ours[rid].deleted = True
                for i in range(0, len(stale), 1000):
                    body = json.dumps({"model": model, "ids": stale[i:i + 1000]}).encode()
                    client.post(base + "/cdp/v1/resync", content=body,
                                headers=security.signed_headers(tenant.id, tenant.secret, body)).raise_for_status()
                out[model] = {"odoo": len(odoo_rows), "resync": len(stale), "deleted": len(missing_in_odoo)}
            tenant.settings = {**(tenant.settings or {}), "last_reconcile_at": utcnow().isoformat()}
    finally:
        if own:
            client.close()
    return out
