"""Zalo: ZNS template messages (by phone) and OA webhook verification.

Configuration of a `zalo_zns` destination:
    config  = {"app_id": "...", "oa_id": "...", "mode": "development" | None,
               "templates": {
                   "checkout_abandoned": {"template_id": "123456", "purpose": "marketing",
                                          "data": {"customer_name": "name",
                                                   "amount": "open_cart.amount",
                                                   "shop": "=DMAG Academy"}},
                   "awaiting_transfer": {"template_id": "234567", "purpose": "care", ...}}}
    secrets = {"secret_key": "<app secret>", "refresh_token": "<OA refresh token>",
               "oa_secret_key": "<OA secret for webhook signatures>"}

Zalo refresh tokens are single-use: every refresh returns a new one, which is
stored (encrypted) in the destination state.
"""

import hashlib
from datetime import timedelta

from sqlalchemy import select

from .. import secrets
from ..models import Activation, Delivery, utcnow
from ..segments import get_path, _MISSING
from ..traits import parse_dt
from . import Handler, contacts, phone_digits, register

OAUTH_URL = "https://oauth.zaloapp.com/v4/oa/access_token"
ZNS_URL = "https://business.openapi.zalo.me/message/template"
# Errors after which retrying is pointless (invalid phone, user not on Zalo, template issues...).
PERMANENT_ERRORS = {-108, -109, -118, -119, -124, -131, -133, -134, -135}


def verify_webhook(app_id: str, oa_secret_key: str, raw_body: bytes, timestamp: str, signature: str | None) -> bool:
    """X-ZEvent-Signature = "mac=" + sha256(app_id + body + timestamp + oa_secret_key)."""
    if not (app_id and oa_secret_key and signature):
        return False
    expected = hashlib.sha256(app_id.encode() + raw_body + str(timestamp).encode() + oa_secret_key.encode()).hexdigest()
    return signature.removeprefix("mac=").strip() == expected


def render(spec: dict, traits: dict) -> dict:
    out = {}
    for param, source in (spec or {}).items():
        if isinstance(source, str) and source.startswith("="):
            out[param] = source[1:]
            continue
        value = get_path(traits, source)
        if value is _MISSING or value is None:
            out[param] = ""
        elif isinstance(value, float) and value.is_integer():
            out[param] = int(value)
        elif isinstance(value, str) and source.endswith("_at") and parse_dt(value):
            out[param] = parse_dt(value).strftime("%d/%m/%Y")
        else:
            out[param] = value
    return out


@register
class ZaloZns(Handler):
    type = "zalo_zns"

    def access_token(self) -> str:
        state = self.dest.state or {}
        expires = parse_dt(state.get("expires_at"))
        if state.get("access_token") and expires and expires > utcnow() + timedelta(minutes=5):
            return secrets.decrypt(state["access_token"])
        refresh = secrets.decrypt(state.get("refresh_token")) or self.secret("refresh_token")
        resp = self.client.post(self.cfg.get("oauth_url", OAUTH_URL), headers={"secret_key": self.secret("secret_key") or ""},
                                data={"refresh_token": refresh, "app_id": self.cfg.get("app_id"),
                                      "grant_type": "refresh_token"}, timeout=30)
        body = resp.json()
        if not body.get("access_token"):
            raise ZaloError(f"token refresh failed: {body}")
        self.set_state(access_token=secrets.encrypt(body["access_token"]),
                       refresh_token=secrets.encrypt(body["refresh_token"]),
                       expires_at=(utcnow() + timedelta(seconds=int(body.get("expires_in", 3600)))).isoformat())
        self.session.flush()  # never lose a rotated refresh token
        return body["access_token"]

    def collect(self) -> int:
        templates = self.cfg.get("templates") or {}
        last = int((self.dest.state or {}).get("last_activation_id", 0))
        acts = self.session.scalars(select(Activation).where(
            Activation.tenant_id == self.tenant.id, Activation.kind == "trigger", Activation.id > last,
        ).order_by(Activation.id).limit(5000)).all()
        n = 0
        for act in acts:
            spec = templates.get(act.segment_key)
            if not spec:
                continue
            self.session.add(Delivery(tenant_id=self.tenant.id, destination_id=self.dest.id, kind="zns",
                                      profile_id=act.profile_id, purpose=spec.get("purpose", "marketing"),
                                      payload={"segment_key": act.segment_key, "template_id": spec["template_id"]}))
            n += 1
        if acts:
            self.set_state(last_activation_id=acts[-1].id)
        self.session.flush()
        return n

    def send(self) -> dict:
        stats = {"sent": 0, "skipped": 0, "failed": 0}
        rows = self.pending("zns", limit=500)
        if not rows:
            return stats
        templates = self.cfg.get("templates") or {}
        token = None
        for d in rows:
            profile = self.profile(d.profile_id)
            if not self.consent_ok(profile, d.purpose):
                self.mark(d, "skipped", f"no {d.purpose} consent")
                stats["skipped"] += 1
                continue
            if d.purpose == "marketing":
                capped = self.capped(d.profile_id)
                if capped:
                    self.mark(d, "skipped", capped)
                    stats["skipped"] += 1
                    continue
            _emails, phones = contacts(self.session, self.tenant.id, d.profile_id)
            if not phones:
                self.mark(d, "skipped", "no phone")
                stats["skipped"] += 1
                continue
            spec = templates.get(d.payload["segment_key"]) or {}
            body = {"phone": phone_digits(phones[0]), "template_id": d.payload["template_id"],
                    "template_data": render(spec.get("data"), profile.traits or {}), "tracking_id": f"cdp-{d.id}"}
            if self.cfg.get("mode"):
                body["mode"] = self.cfg["mode"]
            token = token or self.access_token()
            resp = self.client.post(self.cfg.get("zns_url", ZNS_URL), headers={"access_token": token}, json=body,
                                    timeout=30)
            result = resp.json() if resp.content else {}
            code = result.get("error")
            if code == 0:
                self.mark(d, "sent", external_id=str((result.get("data") or {}).get("msg_id")))
                stats["sent"] += 1
            elif code in PERMANENT_ERRORS:
                self.mark(d, "skipped", f"zalo {code}: {result.get('message')}")
                stats["skipped"] += 1
            else:
                self.mark(d, "failed", f"zalo {code}: {result.get('message')}")
                stats["failed"] += 1
            self.session.flush()  # the frequency cap must see messages sent in this run
        return stats


class ZaloError(Exception):
    pass
