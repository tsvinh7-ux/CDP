"""Meta (Facebook) Conversions API and Custom Audiences.

Only hashed identifiers (SHA-256) leave CDP, and only for people whose
"ads" consent allows it (see consent.py).
"""

import json
from datetime import timedelta

from sqlalchemy import select

from ..models import AudienceMember, Delivery, Event, Profile, Segment, SegmentMember, utcnow
from ..traits import aware
from . import Handler, contacts, phone_digits, register, sha256

GRAPH = "https://graph.facebook.com"
DEFAULT_VERSION = "v21.0"
DEFAULT_EVENT_MAP = {
    "order.paid": "Purchase",
    "order.placed": "InitiateCheckout",
    "Checkout Started": "InitiateCheckout",
    "cart.updated": "AddToCart",
    "Course Viewed": "ViewContent",
    "account.signed_up": "CompleteRegistration",
    "lead.created": "Lead",
}
MAX_AGE = timedelta(days=7)  # CAPI rejects events older than 7 days


class _Graph(Handler):
    def url(self, path: str) -> str:
        return f"{self.cfg.get('graph_url', GRAPH)}/{self.cfg.get('api_version', DEFAULT_VERSION)}/{path}"

    def call(self, method: str, path: str, **data):
        data["access_token"] = self.secret("access_token")
        resp = self.client.request(method, self.url(path), data=data, timeout=30)
        body = resp.json() if resp.content else {}
        if resp.status_code >= 400 or "error" in body:
            raise GraphError(body.get("error", {}).get("message") or f"HTTP {resp.status_code}")
        return body

    def user_data(self, profile: Profile, context: dict | None = None) -> dict:
        emails, phones = contacts(self.session, self.tenant.id, profile.id)
        ud = {"external_id": [sha256(profile.id)]}
        if emails:
            ud["em"] = [sha256(e) for e in emails]
        if phones:
            ud["ph"] = [sha256(phone_digits(p)) for p in phones]
        fb = (context or {}).get("fb") or (profile.traits or {}).get("fb") or {}
        for key in ("fbp", "fbc"):
            if fb.get(key):
                ud[key] = fb[key]
        ctx = context or {}
        if ctx.get("userAgent"):
            ud["client_user_agent"] = ctx["userAgent"]
        return ud


class GraphError(Exception):
    pass


@register
class ConversionsApi(_Graph):
    """Server-side events (deduplicated with the Pixel through event_id)."""

    type = "facebook_capi"

    def collect(self) -> int:
        mapping = {**DEFAULT_EVENT_MAP, **(self.cfg.get("event_map") or {})}
        last = int((self.dest.state or {}).get("last_event_id", 0))
        events = self.session.scalars(select(Event).where(
            Event.tenant_id == self.tenant.id, Event.id > last, Event.event_type.in_(list(mapping)),
        ).order_by(Event.id).limit(5000)).all()
        for ev in events:
            p = (ev.payload or {}).get("properties") or {}
            ctx = (ev.payload or {}).get("context") or {}
            page = ctx.get("page") or {}
            data = {
                "event_name": mapping[ev.event_type],
                "event_time": int(aware(ev.occurred_at).timestamp()),
                "event_id": ev.event_id,
                "action_source": "website" if (ev.source == "web" or p.get("website_id")) else "system_generated",
            }
            if page.get("url"):
                data["event_source_url"] = page["url"]
            custom = {}
            if p.get("amount") is not None:
                custom.update(value=float(p["amount"]), currency=p.get("currency") or "VND")
            ids = [str(i.get("product_id")) for i in p.get("items") or [] if i.get("product_id")]
            if p.get("course_id"):
                ids.append(f"course_{p['course_id']}")
            if ids:
                custom.update(content_ids=ids, content_type="product")
            if custom:
                data["custom_data"] = custom
            self.session.add(Delivery(tenant_id=self.tenant.id, destination_id=self.dest.id, kind="capi_event",
                                      profile_id=ev.profile_id, purpose="ads",
                                      payload={"event": data, "context": ctx}))
        if events:
            self.set_state(last_event_id=events[-1].id)
        self.session.flush()
        return len(events)

    def send(self) -> dict:
        stats = {"sent": 0, "skipped": 0}
        rows, batch = self.pending("capi_event"), []
        now = utcnow()
        for d in rows:
            profile = self.profile(d.profile_id)
            if not self.consent_ok(profile, "ads"):
                self.mark(d, "skipped", "no ads consent")
                stats["skipped"] += 1
                continue
            event = dict(d.payload["event"])
            if now.timestamp() - event["event_time"] > MAX_AGE.total_seconds():
                self.mark(d, "skipped", "older than 7 days")
                stats["skipped"] += 1
                continue
            event["user_data"] = self.user_data(profile, d.payload.get("context"))
            batch.append((d, event))
        for i in range(0, len(batch), 1000):
            chunk = batch[i:i + 1000]
            params = {"data": json.dumps([e for _d, e in chunk])}
            if self.cfg.get("test_event_code"):
                params["test_event_code"] = self.cfg["test_event_code"]
            try:
                body = self.call("POST", f"{self.cfg['pixel_id']}/events", **params)
            except GraphError as exc:
                for d, _e in chunk:
                    self.mark(d, "failed", str(exc))
                continue
            for d, _e in chunk:
                self.mark(d, "sent", external_id=body.get("fbtrace_id"))
            stats["sent"] += len(chunk)
        return stats


@register
class CustomAudiences(_Graph):
    """Keeps one Custom Audience per CDP segment in sync (add/remove deltas)."""

    type = "facebook_audience"

    def collect(self) -> int:
        return 0

    def send(self) -> dict:
        stats = {"added": 0, "removed": 0}
        audiences = dict((self.dest.state or {}).get("audiences") or {})
        for key in self.cfg.get("segments") or []:
            seg = self.session.get(Segment, (self.tenant.id, key))
            if seg is None:
                continue
            if key not in audiences:
                body = self.call("POST", f"{self.cfg['ad_account_id']}/customaudiences",
                                 name=f"{self.cfg.get('name_prefix', 'CDP')} - {seg.name}",
                                 subtype="CUSTOM", customer_file_source="USER_PROVIDED_ONLY",
                                 description=f"CDP segment {key}, synced automatically")
                audiences[key] = body["id"]
                self.set_state(audiences=audiences)
            added, removed = self._sync(key, audiences[key])
            stats["added"] += added
            stats["removed"] += removed
        for d in self.pending("audience_remove"):
            audience = audiences.get(d.payload["segment_key"])
            if audience:
                self._upload("DELETE", audience, [d.payload["hashed"]])
            self.mark(d, "sent")
            stats["removed"] += 1
        return stats

    def _sync(self, key: str, audience_id: str) -> tuple[int, int]:
        members = self.session.scalars(select(Profile).join(
            SegmentMember, (SegmentMember.profile_id == Profile.id) & (SegmentMember.segment_key == key)
            & (SegmentMember.tenant_id == self.tenant.id))).all()
        wanted = {}
        for p in members:
            if not self.consent_ok(p, "ads"):
                continue
            emails, phones = contacts(self.session, self.tenant.id, p.id)
            if emails or phones:
                wanted[p.id] = [sha256(emails[0]) if emails else "",
                                sha256(phone_digits(phones[0])) if phones else ""]
        current = {m.profile_id: m for m in self.session.scalars(select(AudienceMember).where(
            AudienceMember.destination_id == self.dest.id, AudienceMember.segment_key == key)).all()}
        to_add = [pid for pid in wanted if pid not in current or current[pid].hashed != wanted[pid]]
        to_remove = [pid for pid in current if pid not in wanted]
        if to_add:
            self._upload("POST", audience_id, [wanted[pid] for pid in to_add])
            for pid in to_add:
                row = current.get(pid) or AudienceMember(destination_id=self.dest.id, segment_key=key, profile_id=pid)
                row.hashed = wanted[pid]
                self.session.add(row)
        if to_remove:
            self._upload("DELETE", audience_id, [current[pid].hashed for pid in to_remove])
            for pid in to_remove:
                self.session.delete(current[pid])
        self.session.flush()
        return len(to_add), len(to_remove)

    def _upload(self, method: str, audience_id: str, rows: list[list[str]]):
        for i in range(0, len(rows), 10000):
            payload = {"schema": ["EMAIL", "PHONE"], "data": rows[i:i + 10000]}  # values are SHA-256 hashes
            self.call(method, f"{audience_id}/users", payload=json.dumps(payload))
