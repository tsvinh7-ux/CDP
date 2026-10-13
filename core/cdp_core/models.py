import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

JsonType = JSON().with_variant(JSONB(), "postgresql")
# SQLite only auto-increments INTEGER primary keys.
BigId = BigInteger().with_variant(Integer(), "sqlite")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_profile_id() -> str:
    return "prf_" + uuid.uuid4().hex


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    # Shared HMAC secret between this tenant's Odoo and Core.
    secret: Mapped[str] = mapped_column(String(255))
    # Public key embedded in the web SDK; it only identifies the tenant.
    write_key: Mapped[str] = mapped_column(String(64), unique=True)
    odoo_url: Mapped[str | None] = mapped_column(String(255))
    settings: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "event_id", name="uq_events_tenant_event"),
        Index("ix_events_profile_time", "tenant_id", "profile_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id"))
    event_id: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(16))  # odoo | web
    kind: Mapped[str] = mapped_column(String(32))  # event | entity.upserted | entity.deleted | page | track | identify
    event_type: Mapped[str] = mapped_column(String(128))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    profile_id: Mapped[str | None] = mapped_column(String(40), index=True)
    payload: Mapped[dict] = mapped_column(JsonType)


class Entity(Base):
    """Latest snapshot of an Odoo record (from entity.upserted messages)."""

    __tablename__ = "entities"

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    model: Mapped[str] = mapped_column(String(128), primary_key=True)
    res_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, default=0)
    fields: Mapped[dict] = mapped_column(JsonType, default=dict)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Profile(Base):
    __tablename__ = "profiles"
    __table_args__ = (
        Index("ix_profiles_tenant_partner", "tenant_id", "partner_id"),
        Index("ix_profiles_tenant_account", "tenant_id", "account_partner_id"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=new_profile_id)
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id"))
    kind: Mapped[str] = mapped_column(String(16), default="person")  # person | account
    partner_id: Mapped[int | None] = mapped_column(Integer)
    # For persons: the clinic (company partner) they work for.
    account_partner_id: Mapped[int | None] = mapped_column(Integer)
    merged_into: Mapped[str | None] = mapped_column(String(40))
    traits: Mapped[dict] = mapped_column(JsonType, default=dict)
    segment_keys: Mapped[list] = mapped_column(JsonType, default=list)
    # Needs to be pushed to Odoo.
    dirty: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Identity(Base):
    __tablename__ = "identities"
    __table_args__ = (UniqueConstraint("tenant_id", "kind", "value", name="uq_identity"),)

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(32))  # partner | user | email | phone | anon
    value: Mapped[str] = mapped_column(String(255))
    profile_id: Mapped[str] = mapped_column(String(40), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MergeSuggestion(Base):
    """Two Odoo partners that look like the same person; a human decides."""

    __tablename__ = "merge_suggestions"
    __table_args__ = (UniqueConstraint("tenant_id", "partner_a", "partner_b", "reason", name="uq_merge_sugg"),)

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    partner_a: Mapped[int] = mapped_column(Integer)
    partner_b: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Segment(Base):
    __tablename__ = "segments"

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    rule: Mapped[dict] = mapped_column(JsonType)
    trigger_on_enter: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    member_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SegmentMember(Base):
    __tablename__ = "segment_members"

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    segment_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    profile_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Activation(Base):
    """Core-side outbox of things to push to Odoo (segment deltas, triggers)."""

    __tablename__ = "activations"
    __table_args__ = (Index("ix_activations_pending", "tenant_id", "pushed_at"),)

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(32))  # segment.add | segment.remove | trigger
    segment_key: Mapped[str] = mapped_column(String(64))
    profile_id: Mapped[str] = mapped_column(String(40))
    partner_id: Mapped[int | None] = mapped_column(Integer)
    data: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
