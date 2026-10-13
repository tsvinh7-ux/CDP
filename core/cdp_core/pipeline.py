"""Ingestion pipeline: validate → store raw event → resolve identity → traits → segments.

In the MVP everything runs synchronously inside the request. The function
boundaries match the future streaming topology (collector → Redpanda →
identity/traits consumers) so the logic can move behind a queue unchanged.
"""

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import consent, identity, predict, privacy, security, segments as seg
from .models import Activation, Entity, Event, Profile, Segment, SegmentMember, Tenant, utcnow
from .normalize import normalize_email, normalize_phone
from .traits import clinic_summary, compute_account, compute_person, parse_dt, recommend

SUPPORTED_MAJOR = "1"
ODOO_KINDS = {"event", "entity.upserted", "entity.deleted"}
WEB_TYPES = {"page", "track", "identify"}


# --------------------------------------------------------------------------- helpers

def _store_event(session: Session, tenant: Tenant, event_id: str, source: str, kind: str,
                 event_type: str, occurred_at: datetime, payload: dict) -> Event | None:
    """Insert the raw event; return None if this event_id was already stored."""
    if session.scalar(select(Event.id).where(Event.tenant_id == tenant.id, Event.event_id == event_id)):
        return None
    ev = Event(tenant_id=tenant.id, event_id=event_id, source=source, kind=kind,
               event_type=event_type, occurred_at=occurred_at, payload=payload)
    try:
        with session.begin_nested():
            session.add(ev)
            session.flush()
    except IntegrityError:
        return None  # concurrent duplicate
    return ev


def _partner_identifiers(fields: dict) -> list[tuple[str, str]]:
    ids = [("email", normalize_email(fields.get("email")))]
    for key in ("phone", "mobile"):
        ids.append(("phone", normalize_phone(fields.get(key))))
    return [(k, v) for k, v in ids if v]


def _subject_identifiers(subject: dict) -> list[tuple[str, str]]:
    raw = subject.get("identifiers") or {}
    ids = [
        ("email", normalize_email(raw.get("email"))),
        ("phone", normalize_phone(raw.get("phone"))),
        ("user", raw.get("portal_user_id")),
    ]
    return [(k, str(v)) for k, v in ids if v]


def _catalog(session: Session, tenant_id: str) -> dict:
    """Course catalog from Odoo snapshots (slide.channel), cached for the session's lifetime."""
    cache = session.info.setdefault("cdp_catalog", {})
    if tenant_id not in cache:
        rows = session.scalars(select(Entity).where(Entity.tenant_id == tenant_id, Entity.model == "slide.channel",
                                                    Entity.deleted.is_(False))).all()
        cache[tenant_id] = {str(e.res_id): {"name": e.fields.get("name"), "tags": e.fields.get("tag_ids_names") or [],
                                            "published": e.fields.get("is_published", True)} for e in rows}
    return cache[tenant_id]


def _partner_fields(session: Session, tenant_id: str, partner_id: int | None) -> dict | None:
    if not partner_id:
        return None
    ent = session.get(Entity, (tenant_id, "res.partner", partner_id))
    return ent.fields if ent and not ent.deleted else None


# --------------------------------------------------------------------------- refresh

def refresh_profile(session: Session, tenant: Tenant, profile: Profile, now: datetime | None = None,
                    cascade: bool = True):
    """Recompute traits + segments of a profile (and its clinic if it has one)."""
    now = now or utcnow()
    cfg = tenant.settings or {}
    if privacy.is_erased(profile):
        return
    if profile.kind == "account":
        _refresh_account(session, tenant, profile, now)
        return

    events = session.scalars(
        select(Event).where(Event.tenant_id == tenant.id, Event.profile_id == profile.id,
                            Event.kind.notin_(["entity.upserted", "entity.deleted"]))
        .order_by(Event.occurred_at, Event.id)
    ).all()
    pf = _partner_fields(session, tenant.id, profile.partner_id)
    traits = compute_person(pf, events, now, cfg)
    if profile.partner_id and pf is None and (profile.traits or {}).get("deleted"):
        traits["deleted"] = True
    traits["consent"] = consent.effective(tenant, consent.explicit(session, tenant.id, profile.id))

    old_account = profile.account_partner_id
    profile.account_partner_id = traits.get("clinic_partner_id")
    if profile.account_partner_id:
        account = identity.resolve(session, tenant.id, [], partner_id=profile.account_partner_id, kind="account")
        traits["clinic"] = clinic_summary(account.traits or {})
    catalog = _catalog(session, tenant.id)
    if catalog:
        traits.update(recommend(traits, catalog, cfg))
    traits.update(predict.predictions_for(session, tenant, traits, events, now))
    _set_traits(profile, traits, now)
    evaluate_segments(session, tenant, profile, now)

    if cascade:
        for account_partner in {old_account, profile.account_partner_id} - {None}:
            account = identity.find_by_partner(session, tenant.id, account_partner)
            if account:
                _refresh_account(session, tenant, account, now)


def _refresh_account(session: Session, tenant: Tenant, account: Profile, now: datetime):
    members = session.scalars(
        select(Profile).where(Profile.tenant_id == tenant.id, Profile.kind == "person",
                              Profile.merged_into.is_(None),
                              Profile.account_partner_id == account.partner_id)
    ).all()
    traits = compute_account(_partner_fields(session, tenant.id, account.partner_id), members)
    _set_traits(account, traits, now)
    summary = clinic_summary(traits)
    catalog = _catalog(session, tenant.id)
    # Owners are targeted by Marketing Automation with clinic-level signals.
    for m in members:
        if (m.traits or {}).get("clinic") != summary:
            updated = {**(m.traits or {}), "clinic": summary}
            if catalog:
                updated.update(recommend(updated, catalog, tenant.settings or {}))
            _set_traits(m, updated, now)
            evaluate_segments(session, tenant, m, now)


def _set_traits(profile: Profile, traits: dict, now: datetime):
    if traits != profile.traits:
        profile.traits = traits
        profile.dirty = True
        profile.updated_at = now


def evaluate_segments(session: Session, tenant: Tenant, profile: Profile, now: datetime):
    t = profile.traits or {}
    if profile.kind != "person" or profile.merged_into or t.get("deleted") or t.get("erased"):
        matched: list[str] = []
        active: list[Segment] = []
    else:
        active = session.scalars(
            select(Segment).where(Segment.tenant_id == tenant.id, Segment.active.is_(True))
        ).all()
        matched = [s.key for s in active if seg.evaluate(s.rule, profile.traits or {}, now)]

    current = set(profile.segment_keys or [])
    entered, left = set(matched) - current, current - set(matched)
    if not entered and not left:
        return
    by_key = {s.key: s for s in active}
    for key in entered:
        session.add(SegmentMember(tenant_id=tenant.id, segment_key=key, profile_id=profile.id, entered_at=now))
        s = by_key.get(key)
        if s and s.trigger_on_enter and profile.partner_id:
            data = {k: profile.traits.get(k) for k in seg.referenced_traits(s.rule)}
            data["segment_name"] = s.name
            session.add(Activation(tenant_id=tenant.id, kind="trigger", segment_key=key,
                                   profile_id=profile.id, partner_id=profile.partner_id, data=data))
    if left:
        session.query(SegmentMember).filter(
            SegmentMember.tenant_id == tenant.id, SegmentMember.profile_id == profile.id,
            SegmentMember.segment_key.in_(left),
        ).delete(synchronize_session=False)
    profile.segment_keys = sorted(matched)
    profile.dirty = True
    profile.updated_at = now


# --------------------------------------------------------------------------- Odoo batch

def _validate_odoo(msg: dict) -> str | None:
    for key in ("spec_version", "event_id", "kind", "event_type", "occurred_at"):
        if not msg.get(key):
            return f"missing {key}"
    if str(msg["spec_version"]).split(".")[0] != SUPPORTED_MAJOR:
        return f"unsupported spec_version {msg['spec_version']}"
    if msg["kind"] not in ODOO_KINDS:
        return f"unknown kind {msg['kind']}"
    if parse_dt(msg["occurred_at"]) is None:
        return "invalid occurred_at"
    if msg["kind"].startswith("entity.") and not (msg.get("object") or {}).get("model"):
        return "entity message without object.model"
    return None


def ingest_odoo_batch(session: Session, tenant: Tenant, messages: list[dict]) -> dict:
    result = {"accepted": [], "duplicates": [], "rejected": []}
    touched: dict[str, Profile] = {}
    for msg in messages:
        error = _validate_odoo(msg)
        if error:
            result["rejected"].append({"event_id": msg.get("event_id"), "error": error})
            continue
        ev = _store_event(session, tenant, msg["event_id"], "odoo", msg["kind"], msg["event_type"],
                          parse_dt(msg["occurred_at"]), msg)
        if ev is None:
            result["duplicates"].append(msg["event_id"])
            continue
        profile = _apply_odoo(session, tenant, msg, ev)
        if profile is not None:
            touched[profile.id] = profile
        result["accepted"].append(msg["event_id"])
    now = utcnow()
    for profile in touched.values():
        profile = identity.canonical(session, profile.id)
        if profile is not None:
            refresh_profile(session, tenant, profile, now)
    return result


def _apply_odoo(session: Session, tenant: Tenant, msg: dict, ev: Event) -> Profile | None:
    obj = msg.get("object") or {}
    props = msg.get("properties") or {}

    if msg["kind"] == "entity.upserted":
        key = (tenant.id, obj["model"], int(obj["id"]))
        ent = session.get(Entity, key)
        seq = int(msg.get("seq") or 0)
        if ent is None:
            ent = Entity(tenant_id=tenant.id, model=obj["model"], res_id=int(obj["id"]))
            session.add(ent)
        elif seq and ent.seq and seq < ent.seq:
            return None  # out-of-order older snapshot
        ent.fields = {**(props.get("fields") or {}), "_write_date": props.get("write_date")}
        ent.seq = seq
        if obj["model"] == "slide.channel":
            session.info.pop("cdp_catalog", None)
        ent.deleted = False
        ent.updated_at = utcnow()
        if obj["model"] == "res.partner":
            existing = identity.find_by_partner(session, tenant.id, int(obj["id"]))
            if privacy.is_erased(existing):
                ent.fields, ent.deleted = {}, True  # keep no personal data of an erased person
                return None
            kind = "account" if ent.fields.get("is_company") else "person"
            return identity.resolve(session, tenant.id, _partner_identifiers(ent.fields),
                                    partner_id=int(obj["id"]), kind=kind)
        return None

    if msg["kind"] == "entity.deleted":
        ent = session.get(Entity, (tenant.id, obj["model"], int(obj["id"])))
        if ent:
            ent.deleted = True
        if obj["model"] == "res.partner":
            profile = identity.find_by_partner(session, tenant.id, int(obj["id"]))
            into = props.get("merged_into")
            if profile and into:
                target = identity.resolve(session, tenant.id, [], partner_id=int(into))
                profile.partner_id = None  # the partner no longer exists in Odoo
                identity.merge(session, profile, target)
                return target
            if profile:
                profile.traits = {**(profile.traits or {}), "deleted": True}
                return profile
        return None

    subject = msg.get("subject") or {}
    partner_id = int(subject["id"]) if subject.get("model") == "res.partner" and subject.get("id") else None
    ids = _subject_identifiers(subject)
    if not partner_id and not ids:
        return None
    profile = identity.resolve(session, tenant.id, ids, partner_id=partner_id)
    ev.profile_id = profile.id
    if privacy.is_erased(profile):
        session.delete(ev)
        return None
    if msg["event_type"] == "consent.updated":
        consent.record(session, tenant.id, profile.id, props.get("purpose"), bool(props.get("granted")),
                       channel=props.get("channel"), source="odoo", evidence=props.get("evidence_ref"),
                       at=parse_dt(msg["occurred_at"]))
    elif msg["event_type"] == "privacy.erasure_requested":
        privacy.erase(session, tenant, profile, source="odoo")
        return None
    return profile


# --------------------------------------------------------------------------- Web SDK

def ingest_web_batch(session: Session, tenant: Tenant, batch: list[dict]) -> dict:
    result = {"accepted": 0, "duplicates": 0, "rejected": []}
    touched: dict[str, Profile] = {}
    for item in batch:
        mtype = item.get("type")
        anon = item.get("anonymousId")
        if mtype not in WEB_TYPES or not anon:
            result["rejected"].append({"messageId": item.get("messageId"), "error": "invalid type/anonymousId"})
            continue
        partner_id = security.read_user_token(tenant.secret, item.get("userToken"))
        if mtype == "identify" and not partner_id:
            result["rejected"].append({"messageId": item.get("messageId"), "error": "invalid userToken"})
            continue
        occurred = parse_dt(item.get("timestamp")) or utcnow()
        # Never trust client clocks far in the future.
        occurred = min(occurred, utcnow())
        event_type = "page" if mtype == "page" else ("identify" if mtype == "identify" else item.get("event") or "track")
        payload = {
            "properties": item.get("properties") or {},
            "context": item.get("context") or {},
            "anonymousId": anon,
        }
        ev = _store_event(session, tenant, item.get("messageId") or uuid.uuid4().hex, "web", mtype,
                          event_type, occurred, payload)
        if ev is None:
            result["duplicates"] += 1
            continue
        profile = identity.resolve(session, tenant.id, [("anon", anon)], partner_id=partner_id)
        if privacy.is_erased(profile):
            session.delete(ev)
            continue
        ev.profile_id = profile.id
        if event_type == "Consent Updated":
            for purpose, granted in ((item.get("properties") or {}).get("purposes") or {}).items():
                consent.record(session, tenant.id, profile.id, purpose, bool(granted), channel="web",
                               source="web", evidence="cookie banner", at=occurred)
        touched[profile.id] = profile
        result["accepted"] += 1
    now = utcnow()
    for profile in touched.values():
        profile = identity.canonical(session, profile.id)
        if profile is not None:
            refresh_profile(session, tenant, profile, now)
    return result


# --------------------------------------------------------------------------- Zalo OA webhook

def ingest_zalo(session: Session, tenant: Tenant, oa_id: str, body: dict) -> dict:
    """One Zalo OA webhook event (follow, unfollow, user message, user_submit_info...)."""
    name = body.get("event_name") or "unknown"
    if name in ("user_received_message", "user_seen_message"):
        return _zalo_receipt(session, tenant, name, body)
    uid = ((body.get("follower") or {}).get("id") or (body.get("sender") or {}).get("id")
           or body.get("user_id_by_app"))
    if not uid:
        return {"ignored": name}
    ids = [("zalo", f"{oa_id}:{uid}")]
    info = body.get("info") or {}
    phone = normalize_phone(info.get("phone"))
    if phone:
        ids.append(("phone", phone))  # the user chose to share it with the OA
    try:
        occurred = datetime.fromtimestamp(int(body.get("timestamp")) / 1000, tz=utcnow().tzinfo)
    except (TypeError, ValueError):
        occurred = utcnow()
    msg_id = (body.get("message") or {}).get("msg_id")
    event_id = f"zalo:{msg_id}" if msg_id else f"zalo:{name}:{uid}:{body.get('timestamp')}"
    # Message texts are not stored: only the fact that the user interacted.
    payload = {"properties": {"event_name": name, "oa_id": oa_id, "shared_phone": bool(phone)},
               "context": {"zalo_uid": uid}}
    ev = _store_event(session, tenant, event_id, "zalo", "track", f"zalo.{name}", occurred, payload)
    if ev is None:
        return {"duplicate": True}
    profile = identity.resolve(session, tenant.id, ids)
    if privacy.is_erased(profile):
        session.delete(ev)
        return {"ignored": "erased"}
    ev.profile_id = profile.id
    refresh_profile(session, tenant, identity.canonical(session, profile.id))
    return {"accepted": True, "profile_id": profile.id}


def _zalo_receipt(session: Session, tenant: Tenant, name: str, body: dict) -> dict:
    """ZNS delivery/seen receipts: matched to the CDP delivery through tracking_id "cdp-<id>"."""
    from .models import Delivery

    message = body.get("message") or {}
    tracking = str(message.get("tracking_id") or body.get("tracking_id") or "")
    if not tracking.startswith("cdp-") or not tracking[4:].isdigit():
        return {"ignored": "not a CDP message"}
    delivery = session.get(Delivery, int(tracking[4:]))
    if delivery is None or delivery.tenant_id != tenant.id or not delivery.profile_id:
        return {"ignored": "unknown delivery"}
    try:
        occurred = datetime.fromtimestamp(int(body.get("timestamp")) / 1000, tz=utcnow().tzinfo)
    except (TypeError, ValueError):
        occurred = utcnow()
    delivery.payload = {**(delivery.payload or {}), name.replace("user_", "").replace("_message", "") + "_at": occurred.isoformat()}
    payload = {"properties": {"tracking_id": tracking, "msg_id": message.get("msg_id"),
                              "segment_key": (delivery.payload or {}).get("segment_key")}, "context": {}}
    ev = _store_event(session, tenant, f"zalo:{name}:{tracking}", "zalo", "track", f"zalo.{name}", occurred, payload)
    if ev is None:
        return {"duplicate": True}
    profile = identity.canonical(session, delivery.profile_id)
    ev.profile_id = profile.id
    refresh_profile(session, tenant, profile)
    return {"accepted": True, "profile_id": profile.id}


# --------------------------------------------------------------------------- Facebook Lead Ads

def ingest_facebook_lead(session: Session, tenant: Tenant, lead: dict, change: dict) -> dict:
    """A lead form submitted in a Facebook/Instagram ad (fields fetched from the Graph API)."""
    fields = {f.get("name"): (f.get("values") or [None])[0] for f in lead.get("field_data") or []}
    email = normalize_email(fields.get("email"))
    phone = normalize_phone(fields.get("phone_number") or fields.get("phone"))
    ids = [(k, v) for k, v in (("email", email), ("phone", phone), ("fb_lead", lead.get("id"))) if v]
    if not ids:
        return {"ignored": "no identifier"}
    occurred = parse_dt(lead.get("created_time")) or utcnow()
    props = {"form_id": change.get("form_id") or lead.get("form_id"), "ad_id": change.get("ad_id") or lead.get("ad_id"),
             "campaign": lead.get("campaign_name"), "ad_name": lead.get("ad_name"),
             "full_name": fields.get("full_name"), "utm_source": "facebook", "utm_medium": "lead_ads",
             "utm_campaign": lead.get("campaign_name")}
    ev = _store_event(session, tenant, f"fb_lead:{lead.get('id')}", "facebook", "track", "facebook.lead_submitted",
                      occurred, {"properties": {k: v for k, v in props.items() if v}, "context": {}})
    if ev is None:
        return {"duplicate": True}
    profile = identity.resolve(session, tenant.id, ids)
    if privacy.is_erased(profile):
        session.delete(ev)
        return {"ignored": "erased"}
    ev.profile_id = profile.id
    traits = profile.traits or {}
    if fields.get("full_name") and not traits.get("name"):
        profile.traits = {**traits, "name": fields["full_name"]}
    refresh_profile(session, tenant, identity.canonical(session, profile.id))
    return {"accepted": True, "profile_id": profile.id}
