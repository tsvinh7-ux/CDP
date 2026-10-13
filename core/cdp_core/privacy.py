"""Data-subject rights: export and erasure (Luật 91/2025/QH15).

Erasure removes events, identifiers, consents and memberships, asks every ads
audience the person was uploaded to for removal, and leaves a tombstone
profile (no personal data) so Odoo clears its copy and later data for the
same partner is ignored.
"""

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import (Activation, AudienceMember, Consent, Delivery, Entity, Event, Identity,
                     MergeSuggestion, PrivacyRequest, Profile, SegmentMember, Tenant, utcnow)
from .traits import iso


def is_erased(profile: Profile | None) -> bool:
    return bool(profile and (profile.traits or {}).get("erased"))


def export(session: Session, tenant: Tenant, profile: Profile, source: str = "admin") -> dict:
    events = session.scalars(select(Event).where(Event.tenant_id == tenant.id, Event.profile_id == profile.id)
                             .order_by(Event.occurred_at)).all()
    data = {
        "profile": {"id": profile.id, "partner_id": profile.partner_id, "kind": profile.kind,
                    "traits": profile.traits, "segments": profile.segment_keys},
        "identities": [{"kind": i.kind, "value": i.value} for i in session.scalars(
            select(Identity).where(Identity.tenant_id == tenant.id, Identity.profile_id == profile.id))],
        "consents": [{"purpose": c.purpose, "granted": c.granted, "channel": c.channel, "source": c.source,
                      "updated_at": iso(c.updated_at)} for c in session.scalars(
            select(Consent).where(Consent.tenant_id == tenant.id, Consent.profile_id == profile.id))],
        "events": [{"at": iso(e.occurred_at), "source": e.source, "type": e.event_type,
                    "properties": (e.payload or {}).get("properties")} for e in events],
    }
    session.add(PrivacyRequest(tenant_id=tenant.id, profile_id=profile.id, partner_id=profile.partner_id,
                               kind="export", source=source, completed_at=utcnow(),
                               details={"events": len(events)}))
    return data


def erase(session: Session, tenant: Tenant, profile: Profile, source: str = "admin") -> dict:
    now = utcnow()
    tid, pid = tenant.id, profile.id
    # 1. Ask ad platforms to drop the person (hashes only are kept, until sent).
    audience_rows = session.scalars(select(AudienceMember).where(AudienceMember.profile_id == pid)).all()
    for row in audience_rows:
        session.add(Delivery(tenant_id=tid, destination_id=row.destination_id, kind="audience_remove",
                             profile_id=None, purpose="ads",
                             payload={"segment_key": row.segment_key, "hashed": row.hashed}))
        session.delete(row)
    # 2. Delete personal data.
    counts = {
        "events": session.execute(delete(Event).where(Event.tenant_id == tid, Event.profile_id == pid)).rowcount,
        # The partner id link is kept (an internal id, not personal data) so the tombstone keeps
        # blocking data that Odoo may still send for this partner.
        "identities": session.execute(delete(Identity).where(Identity.tenant_id == tid, Identity.profile_id == pid,
                                                             Identity.kind != "partner")).rowcount,
    }
    session.execute(delete(Consent).where(Consent.tenant_id == tid, Consent.profile_id == pid))
    session.execute(delete(SegmentMember).where(SegmentMember.tenant_id == tid, SegmentMember.profile_id == pid))
    session.execute(delete(Activation).where(Activation.profile_id == pid, Activation.pushed_at.is_(None)))
    session.execute(delete(Delivery).where(Delivery.profile_id == pid, Delivery.status == "pending"))
    if profile.partner_id:
        session.execute(delete(MergeSuggestion).where(
            MergeSuggestion.tenant_id == tid,
            (MergeSuggestion.partner_a == profile.partner_id) | (MergeSuggestion.partner_b == profile.partner_id)))
        ent = session.get(Entity, (tid, "res.partner", profile.partner_id))
        if ent:
            session.delete(ent)
    # Profiles previously merged into this one carry no data of their own any more.
    for merged in session.scalars(select(Profile).where(Profile.merged_into == pid)).all():
        merged.traits = {"erased": True}
    # 3. Tombstone: keeps partner_id so Odoo can clear its copy and new data is ignored.
    profile.traits = {"erased": True, "erased_at": iso(now)}
    profile.segment_keys = []
    profile.dirty = True
    profile.updated_at = now
    session.add(PrivacyRequest(tenant_id=tid, profile_id=pid, partner_id=profile.partner_id, kind="erase",
                               source=source, completed_at=now,
                               details={**counts, "audiences": len(audience_rows)}))
    session.flush()
    return counts
