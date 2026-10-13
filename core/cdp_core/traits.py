"""Computed traits.

The MVP recomputes a profile's traits from its full event history. That is
fine for tens of thousands of profiles; at scale this moves to incremental
aggregation / dbt models on ClickHouse with the same output shape.
"""

from datetime import datetime, timedelta, timezone

PAYMENT_PROVIDER_LABELS = {"vnpay": "VNPay", "sepay": "SePay (chuyển khoản)"}
ORDER_STATUS = {
    "cart.updated": "cart",
    "order.placed": "confirmed",     # payment started (VNPay redirect / SePay QR), money not received
    "order.confirmed": "confirmed",  # confirmed in the back-office
    "order.paid": "paid",
    "order.cancelled": "cancelled",
}
_STATUS_RANK = {"cart": 1, "confirmed": 2, "paid": 3, "cancelled": 4}


def aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_dt(value) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return aware(value)
    try:
        return aware(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except ValueError:
        return None


def iso(dt: datetime | None) -> str | None:
    return aware(dt).isoformat() if dt else None


def _money(amount) -> str:
    try:
        return f"{int(round(float(amount))):,}".replace(",", ".") + "đ"
    except (TypeError, ValueError):
        return "?"


def _label(event) -> str:
    p = event.payload.get("properties") or {}
    t = event.event_type
    name = p.get("course_name") or p.get("name") or ""
    if event.kind == "page":
        return f"Xem trang {p.get('title') or p.get('path') or ''}".strip()
    labels = {
        "Course Viewed": f"Xem khóa {name}",
        "Lesson Viewed": f"Mở bài học {p.get('lesson_name') or ''}",
        "Video Progress": f"Xem video {p.get('milestone')}% ({p.get('provider', '')})",
        "Product Added": f"Thêm {name} vào giỏ",
        "Checkout Started": f"Bắt đầu thanh toán {_money(p.get('amount'))}",
        "identify": "Đăng nhập website",
        "cart.updated": f"Cập nhật giỏ hàng {_money(p.get('amount'))}",
        "order.placed": "Đặt đơn {} {} – chờ thanh toán qua {}".format(
            p.get("order_name", ""), _money(p.get("amount")),
            PAYMENT_PROVIDER_LABELS.get(p.get("provider"), p.get("provider") or "?"),
        ),
        "order.confirmed": f"Xác nhận đơn {p.get('order_name', '')} {_money(p.get('amount'))}",
        "order.paid": "Thanh toán {} {} qua {}".format(
            p.get("order_name", ""), _money(p.get("amount")),
            PAYMENT_PROVIDER_LABELS.get(p.get("provider"), p.get("provider") or "?"),
        ),
        "order.cancelled": f"Hủy đơn {p.get('order_name', '')}",
        "course.enrolled": f"Ghi danh khóa {name}",
        "course.progressed": f"Tiến độ {name}: {p.get('completion')}%",
        "course.completed": f"Hoàn thành khóa {name}",
        "lesson.completed": f"Hoàn thành bài {p.get('lesson_name') or ''}",
        "certificate.issued": f"Nhận chứng chỉ {name}",
        "lead.created": "Tạo lead CRM",
    }
    return labels.get(t, t)


def compute_person(partner_fields: dict | None, events: list, now: datetime, cfg: dict) -> dict:
    stall_days = int(cfg.get("stall_days", 14))
    hot_views = int(cfg.get("hot_course_views", 3))

    t: dict = {}
    pf = partner_fields or {}
    for src, dst in (("name", "name"), ("email", "email"), ("phone", "phone"), ("city", "city")):
        if pf.get(src):
            t[dst] = pf[src]
    if pf.get("mobile") and not t.get("phone"):
        t["phone"] = pf["mobile"]
    role = pf.get("dental_role") or pf.get("job_role")
    if role:
        t["job_role"] = role
    if pf.get("parent_id"):
        t["clinic_partner_id"] = pf["parent_id"]

    orders: dict = {}
    learning: dict = {}
    course_names: dict = {}
    course_tags: set = set()
    lessons_completed = 0
    certificates = 0
    course_views: dict = {}
    pageviews_30d = 0
    videos_completed = 0
    first_touch = last_touch = None
    last_seen = None

    for ev in events:
        at = aware(ev.occurred_at)
        p = ev.payload.get("properties") or {}
        last_seen = max(last_seen, at) if last_seen else at
        et = ev.event_type

        if ev.kind == "page":
            if at >= now - timedelta(days=30):
                pageviews_30d += 1
            if p.get("utm_source"):
                touch = {k: p.get(f"utm_{k}") for k in ("source", "medium", "campaign")}
                touch["at"] = iso(at)
                first_touch = first_touch or touch
                last_touch = touch
            continue

        if et == "Course Viewed" and p.get("course_id"):
            cid = str(p["course_id"])
            course_names.setdefault(cid, p.get("course_name"))
            if at >= now - timedelta(days=14):
                course_views[cid] = course_views.get(cid, 0) + 1
        elif et == "Video Progress" and str(p.get("milestone")) == "100":
            videos_completed += 1
        elif et in ORDER_STATUS:
            oid = str(p.get("order_id"))
            o = orders.setdefault(oid, {"order_id": p.get("order_id")})
            o["order_name"] = p.get("order_name") or o.get("order_name")
            if p.get("amount") is not None:
                o["amount"] = p["amount"]
            if p.get("items"):
                o["items"] = p["items"]
            status = ORDER_STATUS[et]
            # Odoo confirms a website order *after* the payment is done, and a
            # late cart.updated must not resurrect a placed order.
            if _STATUS_RANK[status] >= _STATUS_RANK.get(o.get("status"), 0) or status == "cancelled":
                o["status"] = status
            o["updated_at"] = iso(at)
            if status == "confirmed":
                o["provider"] = p.get("provider") or o.get("provider")
                o.setdefault("confirmed_at", iso(at))
            if status == "paid":
                o["paid_at"] = iso(at)
                o["provider"] = p.get("provider") or o.get("provider")
        elif et == "Checkout Started" and p.get("order_id"):
            o = orders.setdefault(str(p["order_id"]), {"order_id": p["order_id"], "status": "cart"})
            o["checkout_started_at"] = iso(at)
            o["updated_at"] = iso(at)
        elif et == "course.enrolled":
            cid = str(p.get("course_id"))
            course_names[cid] = p.get("course_name") or course_names.get(cid)
            course_tags.update(p.get("course_tags") or [])
            c = learning.setdefault(cid, {"completion": 0, "completed": False})
            c["enrolled_at"] = iso(at)
            c["last_activity_at"] = iso(at)
        elif et in ("course.progressed", "course.completed", "lesson.completed", "certificate.issued"):
            cid = str(p.get("course_id"))
            c = learning.setdefault(cid, {"completion": 0, "completed": False})
            c["last_activity_at"] = iso(at)
            if p.get("course_name"):
                course_names[cid] = p["course_name"]
            if et == "course.progressed":
                c["completion"] = max(c["completion"], int(p.get("completion") or 0))
            elif et == "course.completed":
                c["completion"], c["completed"] = 100, True
            elif et == "lesson.completed":
                lessons_completed += 1
            else:
                certificates += 1

    paid = [o for o in orders.values() if o.get("status") == "paid"]
    total = sum(float(o.get("amount") or 0) for o in paid)
    paid_dates = sorted(parse_dt(o["paid_at"]) for o in paid if o.get("paid_at"))
    t.update(
        orders_count=len(paid),
        total_spent=int(total),
        first_order_at=iso(paid_dates[0]) if paid_dates else None,
        last_order_at=iso(paid_dates[-1]) if paid_dates else None,
    )
    carts = [o for o in orders.values() if o.get("status") == "cart" and float(o.get("amount") or 0) > 0]
    t["open_cart"] = max(carts, key=lambda o: o["updated_at"]) if carts else None
    t["awaiting_payment"] = sorted(
        (
            {"order_id": o["order_id"], "order_name": o.get("order_name"), "amount": o.get("amount"),
             "provider": o.get("provider"), "since": o.get("confirmed_at")}
            for o in orders.values() if o.get("status") == "confirmed"
        ),
        key=lambda o: o["since"] or "",
    )

    enrolled = sorted(learning)
    t["courses_enrolled"] = [{"id": c, "name": course_names.get(c)} for c in enrolled]
    t["course_tags_enrolled"] = sorted(course_tags)
    t["courses_completed"] = [c for c in enrolled if learning[c]["completed"]]
    t["learning"] = {c: learning[c] for c in enrolled}
    t["stalled_courses"] = [
        {"id": c, "name": course_names.get(c), "completion": learning[c]["completion"]}
        for c in enrolled
        if not learning[c]["completed"]
        and parse_dt(learning[c]["last_activity_at"]) < now - timedelta(days=stall_days)
    ]
    t["lessons_completed"] = lessons_completed
    t["certificates"] = certificates

    t["course_views_14d"] = course_views
    t["hot_courses"] = [
        {"id": c, "name": course_names.get(c), "views": n}
        for c, n in sorted(course_views.items(), key=lambda kv: -kv[1])
        if n >= hot_views and c not in learning
    ]
    t["pageviews_30d"] = pageviews_30d
    t["videos_completed"] = videos_completed
    t["first_touch"] = first_touch
    t["last_touch"] = last_touch
    t["last_seen_at"] = iso(last_seen)

    recency = (now - paid_dates[-1]).days if paid_dates else None
    t["recency_days"] = recency
    t["rfm_segment"] = _rfm(len(paid), recency)
    engagement = lessons_completed * 5 + videos_completed * 3 + pageviews_30d + certificates * 10
    if last_seen and last_seen >= now - timedelta(days=7):
        engagement += 20
    t["engagement_score"] = min(100, engagement)

    t["timeline"] = [
        {"at": iso(ev.occurred_at), "type": ev.event_type, "label": _label(ev)}
        for ev in sorted(events, key=lambda e: aware(e.occurred_at), reverse=True)[:15]
    ]
    return t


def _rfm(frequency: int, recency: int | None) -> str:
    if not frequency:
        return "prospect"
    if recency is not None and recency > 180:
        return "at_risk" if frequency >= 2 else "lost"
    if frequency >= 3:
        return "champion"
    if frequency == 2:
        return "loyal"
    return "new_customer" if recency is not None and recency <= 30 else "one_time"


def compute_account(company_fields: dict | None, members: list) -> dict:
    """Clinic-level view built from its staff profiles."""
    cf = company_fields or {}
    tags: set = set()
    courses: dict = {}
    learners = 0
    total = 0
    owners = []
    for m in members:
        mt = m.traits or {}
        if mt.get("courses_enrolled"):
            learners += 1
        tags.update(mt.get("course_tags_enrolled") or [])
        for c in mt.get("courses_enrolled") or []:
            courses[c["id"]] = c.get("name")
        total += int(mt.get("total_spent") or 0)
        if mt.get("job_role") == "clinic_owner" and m.partner_id:
            owners.append(m.partner_id)
    return {
        "name": cf.get("name"),
        "city": cf.get("city"),
        "staff_count": len(members),
        "learners_count": learners,
        "staff_course_tags": sorted(tags),
        "staff_courses": [{"id": k, "name": v} for k, v in sorted(courses.items())],
        "total_spent": total,
        "owner_partner_ids": sorted(owners),
    }


CLINIC_SUMMARY_KEYS = ("name", "staff_count", "learners_count", "staff_course_tags", "staff_courses", "total_spent")


def clinic_summary(account_traits: dict) -> dict:
    return {k: account_traits.get(k) for k in CLINIC_SUMMARY_KEYS}
