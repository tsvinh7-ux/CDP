"""Push results back to the tenant's Odoo (docs/data-contract-v1.md §4.2).

Order matters: segment definitions first (Odoo links profiles to them), then
profiles with their full segment list (idempotent, self-healing), then
real-time triggers.
"""

import hashlib
import json
import logging

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import security
from .models import Activation, Profile, Segment, Tenant, utcnow
from .traits import iso

_logger = logging.getLogger(__name__)
PROFILE_BATCH = 200


def _post(client: httpx.Client, tenant: Tenant, method: str, path: str, payload: dict) -> dict:
    body = json.dumps(payload, ensure_ascii=False, default=str).encode()
    url = tenant.odoo_url.rstrip("/") + path
    resp = client.request(method, url, content=body, headers=security.signed_headers(tenant.id, tenant.secret, body))
    resp.raise_for_status()
    return resp.json() if resp.content else {}


def push_tenant(session: Session, tenant: Tenant, client: httpx.Client) -> dict:
    stats = {"segments": 0, "profiles": 0, "triggers": 0, "errors": []}
    if not tenant.odoo_url:
        return stats

    # 1. Segment definitions (only when changed).
    pushed = dict((tenant.settings or {}).get("pushed_segments", {}))
    for s in session.scalars(select(Segment).where(Segment.tenant_id == tenant.id)).all():
        doc = {"name": s.name, "description": s.description, "active": s.active,
               "trigger_on_enter": s.trigger_on_enter, "member_count": s.member_count}
        digest = hashlib.sha1(json.dumps(doc, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if pushed.get(s.key) == digest:
            continue
        try:
            _post(client, tenant, "PUT", f"/cdp/v1/segments/{s.key}", doc)
            pushed[s.key] = digest
            stats["segments"] += 1
        except httpx.HTTPError as exc:
            stats["errors"].append(f"segment {s.key}: {exc}")
    tenant.settings = {**(tenant.settings or {}), "pushed_segments": pushed}

    # 2. Profiles that changed since the last push.
    dirty = session.scalars(
        select(Profile).where(Profile.tenant_id == tenant.id, Profile.dirty.is_(True),
                              Profile.partner_id.is_not(None), Profile.merged_into.is_(None))
        .order_by(Profile.updated_at)
    ).all()
    for i in range(0, len(dirty), PROFILE_BATCH):
        chunk = dirty[i:i + PROFILE_BATCH]
        payload = {"profiles": [
            {"partner_id": p.partner_id, "cdp_id": p.id, "kind": p.kind, "updated_at": iso(p.updated_at),
             "traits": p.traits or {}, "segments": p.segment_keys or []}
            for p in chunk
        ]}
        try:
            result = _post(client, tenant, "POST", "/cdp/v1/profiles", payload)
            for p in chunk:
                p.dirty = False
            stats["profiles"] += len(chunk)
            if result.get("unknown"):
                stats["errors"].append(f"unknown partners in Odoo: {result['unknown']}")
        except httpx.HTTPError as exc:
            stats["errors"].append(f"profiles: {exc}")
            break

    # 3. Real-time triggers.
    pending = session.scalars(
        select(Activation).where(Activation.tenant_id == tenant.id, Activation.kind == "trigger",
                                 Activation.pushed_at.is_(None), Activation.attempts < 10)
        .order_by(Activation.id)
    ).all()
    for act in pending:
        payload = {"trigger_id": f"trg_{act.id}", "key": act.segment_key, "partner_id": act.partner_id,
                   "occurred_at": iso(act.created_at), "data": act.data}
        try:
            _post(client, tenant, "POST", "/cdp/v1/triggers", payload)
            act.pushed_at = utcnow()
            stats["triggers"] += 1
        except httpx.HTTPError as exc:
            act.attempts += 1
            act.last_error = str(exc)
            stats["errors"].append(f"trigger {act.id}: {exc}")
    if stats["errors"]:
        _logger.warning("Push to %s finished with errors: %s", tenant.id, stats["errors"])
    return stats
