"""Consent per purpose (Luật Bảo vệ dữ liệu cá nhân 91/2025/QH15).

Purposes:
    care       – service messages the customer expects (order/appointment/payment reminders)
    marketing  – promotional messages (email, ZNS, SMS)
    ads        – uploading the customer to ad platforms (Custom Audiences, Conversions API)
    analytics  – behavioural analytics / profiling

Each tenant has a policy per purpose: "opt_in" (allowed only after an explicit
grant) or "opt_out" (allowed unless the customer refused). Defaults keep
marketing and ads opt-in.
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Consent, Tenant, utcnow
from .traits import aware

PURPOSES = ("care", "marketing", "ads", "analytics")
DEFAULT_POLICY = {"care": "opt_out", "marketing": "opt_in", "ads": "opt_in", "analytics": "opt_out"}


def policy(tenant: Tenant) -> dict:
    return {**DEFAULT_POLICY, **((tenant.settings or {}).get("consent_policy") or {})}


def record(session: Session, tenant_id: str, profile_id: str, purpose: str, granted: bool,
           channel: str | None = None, source: str | None = None, evidence: str | None = None,
           at: datetime | None = None) -> bool:
    """Upsert a decision; an older decision never overrides a newer one."""
    if purpose not in PURPOSES:
        return False
    at = aware(at) or utcnow()
    row = session.get(Consent, (tenant_id, profile_id, purpose))
    if row is not None and aware(row.updated_at) > at:
        return False
    if row is None:
        row = Consent(tenant_id=tenant_id, profile_id=profile_id, purpose=purpose)
        session.add(row)
    row.granted, row.channel, row.source, row.evidence, row.updated_at = bool(granted), channel, source, evidence, at
    session.flush()
    return True


def explicit(session: Session, tenant_id: str, profile_id: str) -> dict:
    rows = session.scalars(select(Consent).where(Consent.tenant_id == tenant_id, Consent.profile_id == profile_id))
    return {r.purpose: r.granted for r in rows}


def effective(tenant: Tenant, decisions: dict) -> dict:
    pol = policy(tenant)
    return {p: decisions[p] if p in decisions else pol[p] == "opt_out" for p in PURPOSES}


def allowed(tenant: Tenant, traits: dict, purpose: str) -> bool:
    consent = (traits or {}).get("consent")
    if consent and purpose in consent:
        return bool(consent[purpose])
    return policy(tenant).get(purpose) == "opt_out"


def move(session: Session, tenant_id: str, source_id: str, target_id: str):
    """On merge, the newest decision per purpose wins."""
    for row in session.scalars(select(Consent).where(Consent.tenant_id == tenant_id,
                                                     Consent.profile_id == source_id)).all():
        record(session, tenant_id, target_id, row.purpose, row.granted, row.channel, row.source,
               row.evidence, row.updated_at)
        session.delete(row)
    session.flush()
