import json
import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy import desc, func, select

from . import jobs, pipeline, security
from .config import settings
from .db import init_db, session_scope
from .models import Event, MergeSuggestion, Profile, Segment, Tenant
from .traits import iso

_logger = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


def _worker_loop(stop: threading.Event):
    while not stop.wait(settings.worker_interval):
        try:
            jobs.run_once()
        except Exception:  # keep the loop alive
            _logger.exception("Embedded worker run failed")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    stop = threading.Event()
    if settings.embedded_worker:
        threading.Thread(target=_worker_loop, args=(stop,), daemon=True).start()
    yield
    stop.set()


app = FastAPI(title="DMAG CDP Core", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"], allow_headers=["*"])


# --------------------------------------------------------------------------- public

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/sdk/cdp.js")
def sdk():
    return FileResponse(settings.sdk_path, media_type="application/javascript")


@app.post("/v1/batch")
async def odoo_batch(request: Request,
                     x_cdp_tenant: str = Header(None),
                     x_cdp_timestamp: str = Header(None),
                     x_cdp_signature: str = Header(None)):
    """Odoo outbox → Core. HMAC-signed, idempotent (docs/data-contract-v1.md §4.1)."""
    body = await request.body()
    with session_scope() as session:
        tenant = session.get(Tenant, x_cdp_tenant) if x_cdp_tenant else None
        if tenant is None or not security.verify(tenant.secret, x_cdp_timestamp, x_cdp_signature,
                                                 body, settings.signature_ttl):
            raise HTTPException(401, "invalid tenant or signature")
        try:
            messages = json.loads(body).get("messages")
        except (ValueError, AttributeError):
            raise HTTPException(400, "invalid JSON")
        if not isinstance(messages, list):
            raise HTTPException(400, "messages must be a list")
        if len(messages) > settings.max_batch_messages:
            raise HTTPException(413, "batch too large")
        return pipeline.ingest_odoo_batch(session, tenant, messages)


@app.post("/v1/collect")
async def collect(request: Request):
    """Web SDK → Core. Accepts text/plain so navigator.sendBeacon avoids CORS preflight."""
    try:
        data = json.loads(await request.body())
    except ValueError:
        raise HTTPException(400, "invalid JSON")
    batch = data.get("batch") or []
    if not isinstance(batch, list) or len(batch) > 100:
        raise HTTPException(400, "batch must be a list of at most 100 items")
    with session_scope() as session:
        tenant = session.scalar(select(Tenant).where(Tenant.write_key == data.get("writeKey")))
        if tenant is None:
            raise HTTPException(401, "unknown writeKey")
        return pipeline.ingest_web_batch(session, tenant, batch)


# --------------------------------------------------------------------------- admin

def admin(x_admin_token: str = Header(None), token: str = Query(None)):
    if (x_admin_token or token) != settings.admin_token:
        raise HTTPException(401, "admin token required")


def _profile_doc(p: Profile, full: bool = False) -> dict:
    t = p.traits or {}
    doc = {
        "id": p.id, "kind": p.kind, "partner_id": p.partner_id, "name": t.get("name"),
        "job_role": t.get("job_role"), "clinic": (t.get("clinic") or {}).get("name"),
        "rfm_segment": t.get("rfm_segment"), "total_spent": t.get("total_spent"),
        "engagement_score": t.get("engagement_score"), "segments": p.segment_keys or [],
        "last_seen_at": t.get("last_seen_at"), "updated_at": iso(p.updated_at), "dirty": p.dirty,
    }
    if full:
        doc["traits"] = t
    return doc


class TenantIn(BaseModel):
    id: str
    name: str
    secret: str
    write_key: str
    odoo_url: str | None = None
    settings: dict = {}


class SegmentIn(BaseModel):
    name: str
    description: str | None = None
    rule: dict
    trigger_on_enter: bool = False
    active: bool = True


@app.get("/console")
def console():
    return FileResponse(STATIC / "console.html", media_type="text/html")


@app.get("/v1/admin/tenants", dependencies=[Depends(admin)])
def list_tenants():
    with session_scope() as s:
        return [{"id": t.id, "name": t.name, "odoo_url": t.odoo_url, "write_key": t.write_key}
                for t in s.scalars(select(Tenant)).all()]


@app.post("/v1/admin/tenants", dependencies=[Depends(admin)])
def create_tenant(body: TenantIn):
    with session_scope() as s:
        if s.get(Tenant, body.id):
            raise HTTPException(409, "tenant exists")
        jobs.create_tenant(s, body.id, body.name, body.secret, body.write_key, body.odoo_url, body.settings)
    return {"id": body.id}


@app.get("/v1/admin/stats", dependencies=[Depends(admin)])
def stats(tenant: str):
    with session_scope() as s:
        def count(model, *where):
            return s.scalar(select(func.count()).select_from(model).where(*where))
        return {
            "events": count(Event, Event.tenant_id == tenant),
            "events_web": count(Event, Event.tenant_id == tenant, Event.source == "web"),
            "events_odoo": count(Event, Event.tenant_id == tenant, Event.source == "odoo"),
            "profiles": count(Profile, Profile.tenant_id == tenant, Profile.merged_into.is_(None)),
            "known_profiles": count(Profile, Profile.tenant_id == tenant, Profile.merged_into.is_(None),
                                    Profile.partner_id.is_not(None), Profile.kind == "person"),
            "anonymous_profiles": count(Profile, Profile.tenant_id == tenant, Profile.merged_into.is_(None),
                                        Profile.partner_id.is_(None)),
            "accounts": count(Profile, Profile.tenant_id == tenant, Profile.kind == "account"),
            "merged_profiles": count(Profile, Profile.tenant_id == tenant, Profile.merged_into.is_not(None)),
            "merge_suggestions": count(MergeSuggestion, MergeSuggestion.tenant_id == tenant),
        }


@app.get("/v1/admin/events", dependencies=[Depends(admin)])
def events(tenant: str, limit: int = 50, profile_id: str | None = None):
    with session_scope() as s:
        q = select(Event).where(Event.tenant_id == tenant)
        if profile_id:
            q = q.where(Event.profile_id == profile_id)
        rows = s.scalars(q.order_by(desc(Event.id)).limit(min(limit, 500))).all()
        return [{"id": e.id, "source": e.source, "kind": e.kind, "type": e.event_type,
                 "occurred_at": iso(e.occurred_at), "profile_id": e.profile_id,
                 "properties": e.payload.get("properties")} for e in rows]


@app.get("/v1/admin/profiles", dependencies=[Depends(admin)])
def profiles(tenant: str, q: str | None = None, segment: str | None = None, limit: int = 100):
    with session_scope() as s:
        query = select(Profile).where(Profile.tenant_id == tenant, Profile.merged_into.is_(None))
        rows = s.scalars(query.order_by(desc(Profile.updated_at)).limit(min(limit, 1000))).all()
        docs = [_profile_doc(p) for p in rows]
        if segment:
            docs = [d for d in docs if segment in d["segments"]]
        if q:
            ql = q.lower()
            docs = [d for d in docs if ql in json.dumps(d, ensure_ascii=False).lower()]
        return docs


@app.get("/v1/admin/profiles/{profile_id}", dependencies=[Depends(admin)])
def profile(profile_id: str):
    with session_scope() as s:
        p = s.get(Profile, profile_id)
        if p is None:
            raise HTTPException(404)
        doc = _profile_doc(p, full=True)
        doc["merged_into"] = p.merged_into
        return doc


@app.get("/v1/admin/segments", dependencies=[Depends(admin)])
def list_segments(tenant: str):
    with session_scope() as s:
        rows = s.scalars(select(Segment).where(Segment.tenant_id == tenant).order_by(Segment.key)).all()
        return [{"key": x.key, "name": x.name, "description": x.description, "rule": x.rule,
                 "trigger_on_enter": x.trigger_on_enter, "active": x.active,
                 "member_count": x.member_count} for x in rows]


@app.put("/v1/admin/segments/{key}", dependencies=[Depends(admin)])
def upsert_segment(key: str, tenant: str, body: SegmentIn):
    with session_scope() as s:
        seg = s.get(Segment, (tenant, key)) or Segment(tenant_id=tenant, key=key)
        seg.name, seg.description, seg.rule = body.name, body.description, body.rule
        seg.trigger_on_enter, seg.active = body.trigger_on_enter, body.active
        s.add(seg)
    return {"key": key}


@app.get("/v1/admin/merge-suggestions", dependencies=[Depends(admin)])
def merge_suggestions(tenant: str):
    with session_scope() as s:
        rows = s.scalars(select(MergeSuggestion).where(MergeSuggestion.tenant_id == tenant)).all()
        return [{"partner_a": m.partner_a, "partner_b": m.partner_b, "reason": m.reason} for m in rows]


@app.post("/v1/admin/run-jobs", dependencies=[Depends(admin)])
def run_jobs(tenant: str | None = None, push: bool = True):
    return JSONResponse(jobs.run_once(tenant, push=push))
