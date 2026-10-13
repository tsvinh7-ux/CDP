"""Dashboard aggregates (one call, one time window, everything consistent).

Computed from PostgreSQL for the MVP; the same shapes can be served from
ClickHouse when volumes grow.
"""

from collections import Counter, defaultdict
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import analytics
from .models import Delivery, Event, MLModel, Profile, Segment, Tenant, utcnow
from .touchpoints import CHANNEL_KEYS, STAGES, classify, meta
from .traits import aware

FUNNEL = [
    ("visit", "Truy cập website", lambda e: e.kind == "page"),
    ("view", "Xem khóa học / dịch vụ", lambda e: e.event_type in ("Course Viewed", "treatment_plan.proposed")),
    ("cart", "Thêm giỏ / đặt lịch", lambda e: e.event_type in ("cart.updated", "Product Added", "appointment.booked")),
    ("order", "Đặt đơn", lambda e: e.event_type in ("order.placed", "Checkout Started", "treatment_plan.accepted")),
    ("paid", "Thanh toán", lambda e: e.event_type in ("order.paid", "installment.paid", "procedure.completed")),
]


def _amount(ev) -> float:
    return float(((ev.payload or {}).get("properties") or {}).get("amount") or 0)


def build(session: Session, tenant: Tenant, days: int = 30) -> dict:
    now = utcnow()
    start, prev_start = now - timedelta(days=days), now - timedelta(days=2 * days)
    events = session.scalars(select(Event).where(Event.tenant_id == tenant.id, Event.occurred_at >= prev_start,
                                                 Event.kind.notin_(["entity.upserted", "entity.deleted"]))).all()
    cur = [e for e in events if aware(e.occurred_at) >= start]
    prev = [e for e in events if aware(e.occurred_at) < start]

    def kpis(evs):
        paid = [e for e in evs if e.event_type == "order.paid"]
        active = {e.profile_id for e in evs if e.profile_id}
        buyers = {e.profile_id for e in paid}
        revenue = sum(_amount(e) for e in paid)
        return {"revenue": revenue, "orders": len(paid), "buyers": len(buyers), "active": len(active),
                "conversion": round(len(buyers) / len(active), 4) if active else 0,
                "aov": round(revenue / len(paid)) if paid else 0}

    k_cur, k_prev = kpis(cur), kpis(prev)
    profiles = session.scalars(select(Profile).where(Profile.tenant_id == tenant.id, Profile.kind == "person",
                                                     Profile.merged_into.is_(None))).all()
    live = [p for p in profiles if not (p.traits or {}).get("erased")]
    predicted_revenue, likely = 0.0, 0
    product_forecast: dict = defaultdict(lambda: {"expected_buyers": 0.0, "expected_revenue": 0.0, "likely": 0})
    touch_forecast: Counter = Counter()
    for p in live:
        t = p.traits or {}
        if (t.get("purchase_probability_30d") or 0) >= 0.5:
            likely += 1
        for pr in t.get("predicted_products") or []:
            f = product_forecast[pr["item"]]
            f["expected_buyers"] += pr["probability"]
            f["expected_revenue"] += pr.get("expected_revenue") or 0
            f["likely"] += pr["probability"] >= 0.5
            predicted_revenue += pr.get("expected_revenue") or 0
        for nt in t.get("next_touchpoints") or []:
            if (nt.get("expected_in_days") or 99) <= 7:
                touch_forecast[nt["channel"]] += nt["probability"]
    # "New" = first touchpoint inside the window (not the row's insert time, which is skewed by backfills).
    first_seen = dict(session.execute(select(Event.profile_id, func.min(Event.occurred_at)).where(
        Event.tenant_id == tenant.id, Event.profile_id.is_not(None)).group_by(Event.profile_id)).all())
    live_ids = {p.id for p in live}
    firsts = [aware(v) for k, v in first_seen.items() if k in live_ids and v is not None]
    k_cur.update(
        profiles=len(live), known=sum(1 for p in live if p.partner_id),
        new_profiles=sum(1 for f in firsts if f >= start),
        predicted_revenue_30d=round(predicted_revenue), likely_buyers_30d=likely,
    )
    k_prev["new_profiles"] = sum(1 for f in firsts if prev_start <= f < start)

    # Daily series: complete days only (ends yesterday, so today's partial day never reads as a drop).
    daily: dict = {(now - timedelta(days=days - i)).date().isoformat(): {"active": set(), "revenue": 0.0, "orders": 0,
                                                                         "touchpoints": 0}
                   for i in range(days)}
    for e in cur:
        d = daily.get(aware(e.occurred_at).date().isoformat())
        if d is None:
            continue
        if e.profile_id:
            d["active"].add(e.profile_id)
        if e.event_type == "order.paid":
            d["revenue"] += _amount(e)
            d["orders"] += 1
        d["touchpoints"] += 1
    series = [{"date": k, "active": len(v["active"]), "revenue": v["revenue"], "orders": v["orders"],
               "touchpoints": v["touchpoints"]} for k, v in sorted(daily.items())]

    # Funnel: distinct people reaching each step in the window.
    reached = {key: set() for key, _l, _f in FUNNEL}
    for e in cur:
        for key, _label, pred in FUNNEL:
            if e.profile_id and pred(e):
                reached[key].add(e.profile_id)
    funnel = [{"key": key, "label": label, "people": len(reached[key])} for key, label, _f in FUNNEL]

    # Touchpoints by channel per period (complete periods only, counted back from now), and by stage.
    step = 1 if days <= 14 else 7
    n_bins = max(1, days // step)

    def bin_start(at):
        k = int((now - aware(at)).total_seconds() // (step * 86400))
        return (now - timedelta(days=step * (k + 1))).date().isoformat() if k < n_bins else None

    weeks: dict = defaultdict(Counter)
    by_stage, by_channel = Counter(), Counter()
    for e in cur:
        tp = classify(e)
        if tp is None:
            continue
        by_stage[tp.stage] += 1
        by_channel[tp.channel] += 1
        b = bin_start(e.occurred_at)
        if b:
            weeks[b][tp.channel] += 1
    for d in session.scalars(select(Delivery).where(Delivery.tenant_id == tenant.id, Delivery.status == "sent",
                                                    Delivery.kind == "zns", Delivery.sent_at >= start)).all():
        by_channel["zalo"] += 1
        b = bin_start(d.sent_at)
        if b:
            weeks[b]["zalo"] += 1
    bins = [(now - timedelta(days=step * (k + 1))).date().isoformat() for k in reversed(range(n_bins))]
    channel_weeks = [{"week": w, **{c: weeks[w].get(c, 0) for c in CHANNEL_KEYS}} for w in bins]

    segs = session.scalars(select(Segment).where(Segment.tenant_id == tenant.id, Segment.active.is_(True))).all()
    models = {m.kind: {"trained_at": m.trained_at.isoformat() if m.trained_at else None, "version": m.version,
                       "metrics": {k: v for k, v in (m.metrics or {}).items() if k != "weights"}}
              for m in session.scalars(select(MLModel).where(MLModel.tenant_id == tenant.id))}
    deliveries = dict(session.execute(select(Delivery.status, func.count()).where(
        Delivery.tenant_id == tenant.id, Delivery.created_at >= start).group_by(Delivery.status)).all())
    return {
        "tenant": {"id": tenant.id, "name": tenant.name, "simulated": bool((tenant.settings or {}).get("simulated"))},
        "window": {"days": days, "start": start.isoformat(), "end": now.isoformat(), "period_days": step},
        "kpis": k_cur, "previous": k_prev,
        "daily": series,
        "funnel": funnel,
        "channel_weeks": channel_weeks,
        "touchpoints_by_channel": [{"channel": c, "count": by_channel.get(c, 0)} for c in CHANNEL_KEYS],
        "touchpoints_by_stage": [{"stage": s, "label": l, "count": by_stage.get(s, 0)} for s, l in STAGES],
        "attribution": analytics.attribution(session, tenant.id)["rows"][:8],
        "product_forecast": sorted(({"item": k, **{kk: round(vv, 1) for kk, vv in v.items()}}
                                    for k, v in product_forecast.items()), key=lambda r: -r["expected_buyers"]),
        "touchpoint_forecast_7d": [{"channel": c, "expected": round(touch_forecast.get(c, 0), 1)} for c in CHANNEL_KEYS],
        "segments": sorted(({"key": s.key, "name": s.name, "members": s.member_count} for s in segs),
                           key=lambda r: -r["members"]),
        "deliveries": deliveries,
        "models": models,
        "meta": meta(),
    }
