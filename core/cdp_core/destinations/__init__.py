"""Activation destinations (ad platforms, messaging).

Each destination type:
  * collect() turns new CDP data (events, segment entries) into Delivery rows,
  * send()    delivers pending rows, after consent and frequency-cap checks.
Deliveries are durable, so a provider outage only delays sending.
"""

import hashlib
import logging
from datetime import timedelta

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import consent, secrets
from ..models import Delivery, Destination, Identity, Profile, Tenant, utcnow

_logger = logging.getLogger(__name__)
REGISTRY: dict[str, type["Handler"]] = {}


def register(cls):
    REGISTRY[cls.type] = cls
    return cls


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def contacts(session: Session, tenant_id: str, profile_id: str) -> tuple[list[str], list[str]]:
    """Normalised e-mails and phones (E.164) known for a profile."""
    rows = session.scalars(select(Identity).where(Identity.tenant_id == tenant_id,
                                                  Identity.profile_id == profile_id,
                                                  Identity.kind.in_(["email", "phone"]))).all()
    return ([r.value for r in rows if r.kind == "email"], [r.value for r in rows if r.kind == "phone"])


def phone_digits(e164: str) -> str:
    """+84901234567 → 84901234567 (format expected by Facebook and Zalo)."""
    return e164.lstrip("+")


class Handler:
    type = ""

    def __init__(self, session: Session, tenant: Tenant, dest: Destination, client: httpx.Client):
        self.session, self.tenant, self.dest, self.client = session, tenant, dest, client
        self.cfg = dest.config or {}

    def secret(self, name: str) -> str | None:
        return secrets.decrypt((self.cfg.get("secrets") or {}).get(name))

    def set_state(self, **values):
        self.dest.state = {**(self.dest.state or {}), **values}

    def profile(self, profile_id: str | None) -> Profile | None:
        return self.session.get(Profile, profile_id) if profile_id else None

    def consent_ok(self, profile: Profile | None, purpose: str) -> bool:
        return bool(profile) and consent.allowed(self.tenant, profile.traits or {}, purpose)

    def capped(self, profile_id: str, now=None) -> str | None:
        """Frequency cap on marketing messages across channels (tenant setting)."""
        cap = (self.tenant.settings or {}).get("frequency_cap") or {"per_day": 1, "per_week": 3}
        now = now or utcnow()
        for key, window in (("per_day", timedelta(days=1)), ("per_week", timedelta(days=7))):
            limit = cap.get(key)
            if limit is None:
                continue
            sent = self.session.scalar(select(func.count()).select_from(Delivery).where(
                Delivery.tenant_id == self.tenant.id, Delivery.profile_id == profile_id,
                Delivery.purpose == "marketing", Delivery.status == "sent",
                Delivery.kind.in_(["zns"]), Delivery.sent_at >= now - window))
            if sent >= int(limit):
                return f"frequency cap {key}={limit}"
        return None

    def pending(self, kind: str, limit: int = 1000) -> list[Delivery]:
        return self.session.scalars(select(Delivery).where(
            Delivery.destination_id == self.dest.id, Delivery.kind == kind,
            Delivery.status.in_(["pending", "failed"]), Delivery.attempts < 5,
        ).order_by(Delivery.id).limit(limit)).all()

    @staticmethod
    def mark(delivery: Delivery, status: str, reason: str | None = None, external_id: str | None = None):
        delivery.status, delivery.reason = status, reason
        if status == "sent":
            delivery.sent_at = utcnow()
            delivery.external_id = external_id
        if status == "failed":
            delivery.attempts += 1

    def collect(self) -> int:
        return 0

    def send(self) -> dict:
        return {}


def run(session: Session, tenant: Tenant, client: httpx.Client) -> dict:
    from . import facebook, zalo  # noqa: F401  (register handlers)

    results = {}
    for dest in session.scalars(select(Destination).where(Destination.tenant_id == tenant.id,
                                                          Destination.enabled.is_(True))).all():
        cls = REGISTRY.get(dest.type)
        if cls is None:
            continue
        handler = cls(session, tenant, dest, client)
        try:
            collected = handler.collect()
            sent = handler.send()
            dest.last_error = None
            results[f"{dest.type}:{dest.id}"] = {"collected": collected, **sent}
        except (httpx.HTTPError, facebook.GraphError, zalo.ZaloError) as exc:
            dest.last_error = str(exc)
            _logger.warning("Destination %s/%s failed: %s", tenant.id, dest.id, exc)
            results[f"{dest.type}:{dest.id}"] = {"error": str(exc)}
        session.flush()
    return results


def create(session: Session, tenant_id: str, type_: str, name: str, config: dict, secret_values: dict) -> Destination:
    from . import facebook, zalo  # noqa: F401

    if type_ not in REGISTRY:
        raise ValueError(f"unknown destination type {type_!r}")
    dest = Destination(tenant_id=tenant_id, type=type_, name=name,
                       config={**config, "secrets": secrets.seal(secret_values)}, state={})
    session.add(dest)
    session.flush()
    return dest
