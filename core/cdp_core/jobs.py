"""Periodic jobs: time-based trait/segment refresh, counts, push to Odoo."""

import logging

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import activation, segments as seg
from .db import session_scope
from .models import Profile, Segment, SegmentMember, Tenant, utcnow
from .pipeline import refresh_profile

_logger = logging.getLogger(__name__)


def create_tenant(session: Session, tenant_id: str, name: str, secret: str, write_key: str,
                  odoo_url: str | None = None, settings: dict | None = None) -> Tenant:
    tenant = Tenant(id=tenant_id, name=name, secret=secret, write_key=write_key,
                    odoo_url=odoo_url, settings=settings or {})
    session.add(tenant)
    cfg = settings or {}
    for s in seg.default_segments(int(cfg.get("abandon_minutes", 60)), int(cfg.get("transfer_minutes", 30))):
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
    results = {}
    own_client = client is None
    client = client or httpx.Client(timeout=30)
    try:
        with session_scope() as session:
            query = select(Tenant)
            if tenant_id:
                query = query.where(Tenant.id == tenant_id)
            for tenant in session.scalars(query).all():
                refresh_tenant(session, tenant)
                session.flush()
                results[tenant.id] = activation.push_tenant(session, tenant, client) if push else {}
    finally:
        if own_client:
            client.close()
    return results
