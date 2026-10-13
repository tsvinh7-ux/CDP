"""Deterministic identity resolution.

Rules (docs/de-xuat-kien-truc-cdp.md §5.5):
* Odoo is the master of *who is a person*: one Odoo partner = one profile.
  Two profiles that carry different partner ids are never merged
  automatically; a MergeSuggestion is recorded for a human to review.
* Anonymous profiles (web visitors, leads without a partner) are merged
  into the partner profile as soon as a shared identifier shows up.
* Account (clinic/company) profiles are keyed only by their partner id, so a
  phone or e-mail shared between a clinic and its owner never links them.
"""

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .models import Event, Identity, MergeSuggestion, Profile, SegmentMember, utcnow


def canonical(session: Session, profile_id: str) -> Profile | None:
    seen = set()
    profile = session.get(Profile, profile_id)
    while profile is not None and profile.merged_into and profile.id not in seen:
        seen.add(profile.id)
        profile = session.get(Profile, profile.merged_into)
    return profile


def _oldest(profiles):
    profiles = [p for p in profiles if p is not None]
    return min(profiles, key=lambda p: (p.created_at, p.id)) if profiles else None


def find_by_partner(session: Session, tenant_id: str, partner_id: int) -> Profile | None:
    ident = session.scalar(
        select(Identity).where(
            Identity.tenant_id == tenant_id, Identity.kind == "partner", Identity.value == str(partner_id)
        )
    )
    return canonical(session, ident.profile_id) if ident else None


def suggest_merge(session: Session, tenant_id: str, a: int, b: int, reason: str):
    a, b = sorted((a, b))
    exists = session.scalar(
        select(MergeSuggestion.id).where(
            MergeSuggestion.tenant_id == tenant_id,
            MergeSuggestion.partner_a == a,
            MergeSuggestion.partner_b == b,
            MergeSuggestion.reason == reason,
        )
    )
    if not exists:
        session.add(MergeSuggestion(tenant_id=tenant_id, partner_a=a, partner_b=b, reason=reason))


def merge(session: Session, source: Profile, target: Profile):
    """Fold `source` into `target`; history (events, identities) moves along."""
    if source.id == target.id:
        return
    session.execute(update(Identity).where(Identity.profile_id == source.id).values(profile_id=target.id))
    session.execute(update(Event).where(Event.profile_id == source.id).values(profile_id=target.id))
    # Memberships are recomputed for the target; the source never reached Odoo
    # (it had no partner, or the same partner as the target).
    session.query(SegmentMember).filter(SegmentMember.profile_id == source.id).delete()
    source.merged_into = target.id
    source.segment_keys = []
    source.updated_at = utcnow()
    target.dirty = True
    session.flush()


def resolve(
    session: Session,
    tenant_id: str,
    identifiers: list[tuple[str, str]],
    partner_id: int | None = None,
    kind: str = "person",
) -> Profile:
    if kind == "account":
        identifiers = []
    ids = [(k, str(v)) for k, v in identifiers if v not in (None, "", False)]
    if partner_id:
        ids.insert(0, ("partner", str(partner_id)))
    ids = list(dict.fromkeys(ids))

    found: dict[tuple[str, str], Profile] = {}
    for key in ids:
        ident = session.scalar(
            select(Identity).where(
                Identity.tenant_id == tenant_id, Identity.kind == key[0], Identity.value == key[1]
            )
        )
        if ident:
            profile = canonical(session, ident.profile_id)
            if profile is not None:
                found[key] = profile

    candidates = list({p.id: p for p in found.values()}.values())
    target = found.get(("partner", str(partner_id))) if partner_id else None
    if target is None:
        if partner_id:
            # A known visitor/lead just became an Odoo partner: adopt it.
            target = _oldest(p for p in candidates if p.partner_id is None)
        else:
            target = _oldest(p for p in candidates if p.partner_id) or _oldest(candidates)
    if target is None:
        target = Profile(tenant_id=tenant_id, kind=kind, partner_id=partner_id)
        session.add(target)
        session.flush()
    if partner_id and target.partner_id is None:
        target.partner_id = partner_id
        target.kind = kind
        target.dirty = True

    for key in ids:
        owner = found.get(key)
        if owner is None:
            session.add(Identity(tenant_id=tenant_id, kind=key[0], value=key[1], profile_id=target.id))
        elif owner.id == target.id:
            continue
        elif owner.partner_id is None or owner.partner_id == target.partner_id:
            merge(session, owner, target)
        elif target.partner_id:
            # The identifier already belongs to another Odoo partner
            # (shared clinic phone, duplicate contact...). Do not merge.
            suggest_merge(session, tenant_id, target.partner_id, owner.partner_id, key[0])
    session.flush()
    return target
