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


# Touchpoints without a bespoke label above (CRM, email, events, Zalo receipts).
EXTRA_LABELS = {
    "facebook.lead_submitted": "Điền form quảng cáo Facebook",
    "marketing.email_sent": "Nhận email {campaign}",
    "marketing.email_opened": "Mở email {campaign}",
    "marketing.email_clicked": "Click email {campaign}",
    "marketing.email_replied": "Trả lời email",
    "marketing.email_bounced": "Email không tới được",
    "marketing.unsubscribed": "Hủy nhận email",
    "lead.stage_changed": "Cơ hội chuyển sang \"{stage}\"",
    "lead.won": "Chốt cơ hội bán hàng",
    "lead.lost": "Mất cơ hội bán hàng",
    "sales.activity_done": "Sales: {activity_type} – {summary}",
    "event.registered": "Đăng ký {event_name}",
    "event.attended": "Tham dự {event_name}",
    "event.cancelled": "Hủy đăng ký {event_name}",
    "zalo.user_received_message": "Nhận tin ZNS",
    "zalo.user_seen_message": "Đã xem tin ZNS",
}


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
        "appointment.booked": f"Đặt lịch hẹn {p.get('service_group') or ''} {(p.get('scheduled_at') or '')[:16]}",
        "appointment.checked_in": "Đến khám",
        "appointment.no_show": "Không đến theo lịch hẹn",
        "appointment.cancelled": "Hủy lịch hẹn",
        "treatment_plan.proposed": f"Được tư vấn kế hoạch điều trị {_money(p.get('amount'))}",
        "treatment_plan.accepted": f"Đồng ý kế hoạch điều trị {_money(p.get('amount'))}",
        "treatment_plan.declined": "Từ chối kế hoạch điều trị",
        "procedure.completed": f"Hoàn thành {p.get('service_group') or 'thủ thuật'}",
        "installment.overdue": f"Trễ hạn trả góp {_money(p.get('amount'))}",
        "installment.paid": f"Thanh toán trả góp {_money(p.get('amount'))}",
        "zalo.follow": "Quan tâm Zalo OA",
        "zalo.unfollow": "Bỏ quan tâm Zalo OA",
        "zalo.user_send_text": "Nhắn tin cho Zalo OA",
        "zalo.user_submit_info": "Chia sẻ thông tin qua Zalo OA",
        "consent.updated": "{} đồng ý: {}".format("Cấp" if p.get("granted") else "Rút", p.get("purpose")),
    }
    if t in labels:
        return labels[t]
    if t in EXTRA_LABELS:
        return EXTRA_LABELS[t].format(**{k: p.get(k) or "" for k in
                                         ("campaign", "stage", "activity_type", "summary", "event_name")}).strip(" –")
    return t


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
    fb_ids: dict = {}
    zalo_follower = None

    for ev in events:
        at = aware(ev.occurred_at)
        p = ev.payload.get("properties") or {}
        last_seen = max(last_seen, at) if last_seen else at
        if ev.event_type == "facebook.lead_submitted" and p.get("full_name") and not t.get("name"):
            t["name"] = p["full_name"]
        if ev.event_type == "facebook.lead_submitted" and p.get("utm_source"):
            first_touch = first_touch or {"source": "facebook", "medium": "lead_ads",
                                          "campaign": p.get("campaign"), "at": iso(at)}
        fb_ids.update((ev.payload.get("context") or {}).get("fb") or {})
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
        elif et in ("zalo.follow", "zalo.unfollow"):
            zalo_follower = et == "zalo.follow"
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
    t["fb"] = fb_ids or None
    t["zalo_follower"] = zalo_follower

    recency = (now - paid_dates[-1]).days if paid_dates else None
    t["recency_days"] = recency
    t["rfm_segment"] = _rfm(len(paid), recency)
    engagement = lessons_completed * 5 + videos_completed * 3 + pageviews_30d + certificates * 10
    if last_seen and last_seen >= now - timedelta(days=7):
        engagement += 20
    t["engagement_score"] = min(100, engagement)

    t.update(compute_dental(events, now, cfg))
    t["lead_score"], t["lead_score_reasons"] = lead_score(t, now)

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


# --------------------------------------------------------------------------- scoring & recommendations

def lead_score(t: dict, now: datetime) -> tuple[int, list[str]]:
    """Explainable 0–100 purchase-intent score (rules; to be replaced by a model trained on outcomes)."""
    score, reasons = 0, []

    def add(points, reason):
        nonlocal score
        score += points
        reasons.append(f"+{points} {reason}")

    if t.get("hot_courses"):
        add(25, "xem nhiều lần một khóa chưa mua")
        extra = max(c.get("views", 0) for c in t["hot_courses"]) - 3
        if extra > 0:
            add(min(10, extra * 2), "lượt xem lặp lại")
    if t.get("open_cart"):
        add(25, "có giỏ hàng chưa thanh toán")
    if t.get("awaiting_payment"):
        add(20, "đã đặt đơn, chờ thanh toán")
    seen = parse_dt(t.get("last_seen_at"))
    if seen and seen >= now - timedelta(days=3):
        add(10, "hoạt động trong 3 ngày")
    elif seen and seen >= now - timedelta(days=14):
        add(5, "hoạt động trong 14 ngày")
    if t.get("job_role") == "clinic_owner":
        add(10, "chủ phòng khám (người quyết định)")
    elif t.get("job_role") == "dentist":
        add(5, "bác sĩ")
    if (t.get("first_touch") or {}).get("medium") in ("cpc", "paid", "ads"):
        add(5, "đến từ quảng cáo")
    engagement = int((t.get("engagement_score") or 0) * 0.2)
    if engagement:
        add(engagement, "mức độ tương tác")
    return min(100, score), reasons


def recommend(t: dict, catalog: dict, cfg: dict) -> dict:
    """Next course(s) for a person, and for a clinic owner the next course for the clinic's staff.

    catalog: {course_id(str): {"name", "tags": [...], "published": bool}}
    Level progression uses the tenant's level tags (default "Cơ bản" → "Nâng cao").
    """
    basic, advanced = (cfg.get("levels") or ["Cơ bản", "Nâng cao"])[:2]
    levels = {basic, advanced}
    enrolled = {str(c["id"]) for c in t.get("courses_enrolled") or []}
    completed = {str(c) for c in t.get("courses_completed") or []}
    live = {cid: c for cid, c in catalog.items() if c.get("published", True)}

    def topics(cid):
        return set(live.get(cid, catalog.get(cid, {})).get("tags") or []) - levels

    def next_level(taken: set[str]) -> dict[str, tuple[float, str]]:
        out = {}
        for cid in taken:
            course = catalog.get(cid) or {}
            if basic not in (course.get("tags") or []):
                continue
            for other, oc in live.items():
                if other in taken or advanced not in (oc.get("tags") or []) or not (topics(cid) & topics(other)):
                    continue
                score = 3 + (1 if cid in completed else 0)
                out[other] = max(out.get(other, (0, "")), (score, f"học tiếp sau {course.get('name')}"))
        return out

    scored = next_level(enrolled)
    for c in t.get("hot_courses") or []:
        cid = str(c["id"])
        if cid in live and cid not in enrolled:
            scored[cid] = max(scored.get(cid, (0, "")), (2 + c.get("views", 0) / 10, "đang quan tâm"))
    for c in (t.get("clinic") or {}).get("staff_courses") or []:
        cid = str(c["id"])
        if cid in live and cid not in enrolled:
            scored[cid] = max(scored.get(cid, (0, "")), (1, "đồng nghiệp ở phòng khám đã học"))
    ranked = sorted(scored.items(), key=lambda kv: -kv[1][0])[:3]
    out = {"next_courses": [{"id": cid, "name": live[cid]["name"], "reason": r} for cid, (_s, r) in ranked]}
    out["next_best_course"] = out["next_courses"][0] if ranked else None

    clinic = t.get("clinic") or {}
    if t.get("job_role") == "clinic_owner" and clinic.get("staff_courses"):
        staff_taken = {str(c["id"]) for c in clinic["staff_courses"]}
        ranked = sorted(next_level(staff_taken).items(), key=lambda kv: -kv[1][0])
        out["clinic_next_course"] = ({"id": ranked[0][0], "name": live[ranked[0][0]]["name"],
                                      "reason": "nhân sự đã học khóa cơ bản cùng chủ đề"} if ranked else None)
    return out


# --------------------------------------------------------------------------- dental vertical

DEFAULT_RECALL_MONTHS = {"scaling": 6, "ortho_check": 1, "implant_check": 12, "whitening": 12, "checkup": 6}
DENTAL_EVENTS = ("appointment.", "treatment_plan.", "procedure.", "installment.")


def compute_dental(events: list, now: datetime, cfg: dict) -> dict:
    """Patient traits from Dental ERP events (data contract §5.3; no clinical data)."""
    appts: dict = {}
    plans: dict = {}
    last_by_group: dict = {}
    installments: dict = {}
    branches: set = set()
    value = 0.0
    for ev in events:
        et = ev.event_type
        if not et.startswith(DENTAL_EVENTS):
            continue
        p = (ev.payload or {}).get("properties") or {}
        at = aware(ev.occurred_at)
        if p.get("branch_id"):
            branches.add(str(p["branch_id"]))
        if et.startswith("appointment."):
            a = appts.setdefault(str(p.get("appointment_id")), {})
            a["status"] = et.split(".", 1)[1]
            if p.get("scheduled_at"):
                a["scheduled_at"] = p["scheduled_at"]
            a["service_group"] = p.get("service_group") or a.get("service_group")
            if a["status"] == "checked_in":
                a["visited_at"] = iso(at)
        elif et.startswith("treatment_plan."):
            plan = plans.setdefault(str(p.get("plan_id")), {"plan_id": p.get("plan_id")})
            plan["status"] = et.split(".", 1)[1]
            plan["amount"] = p.get("amount", plan.get("amount"))
            plan["service_groups"] = p.get("service_groups") or plan.get("service_groups")
            if plan["status"] == "proposed":
                plan["proposed_at"] = iso(at)
        elif et == "procedure.completed":
            group = p.get("service_group") or "other"
            last_by_group[group] = max(last_by_group.get(group) or iso(at), iso(at))
            value += float(p.get("amount") or 0)
        elif et.startswith("installment."):
            key = f"{p.get('plan_id')}:{p.get('due_date')}"
            installments[key] = {"plan_id": p.get("plan_id"), "due_date": p.get("due_date"),
                                 "amount": p.get("amount"), "status": et.split(".", 1)[1]}
    if not (appts or plans or last_by_group or installments):
        return {}

    upcoming = sorted(a["scheduled_at"] for a in appts.values()
                      if a.get("status") in ("booked", "rescheduled") and a.get("scheduled_at")
                      and parse_dt(a["scheduled_at"]) >= now)
    visits = [parse_dt(a["visited_at"]) for a in appts.values() if a.get("visited_at")]
    visits += [parse_dt(v) for v in last_by_group.values()]
    no_shows = sum(1 for a in appts.values() if a.get("status") == "no_show")
    closed = sum(1 for a in appts.values() if a.get("status") in ("no_show", "checked_in"))
    recall_months = {**DEFAULT_RECALL_MONTHS, **(cfg.get("recall_months") or {})}
    recall_due = []
    if not upcoming:
        for group, last in last_by_group.items():
            months = recall_months.get(group)
            if months and parse_dt(last) + timedelta(days=30 * months) <= now:
                recall_due.append({"service_group": group, "last_at": last,
                                   "due_since": iso(parse_dt(last) + timedelta(days=30 * months))})
    return {
        "patient": True,
        "next_appointment_at": upcoming[0] if upcoming else None,
        "last_visit_at": iso(max(visits)) if visits else None,
        "appointments_count": len(appts),
        "no_show_count": no_shows,
        "no_show_rate": round(no_shows / closed, 2) if closed else 0,
        "open_treatment_plans": sorted(
            ({k: v for k, v in pl.items() if k != "status"} for pl in plans.values() if pl.get("status") == "proposed"),
            key=lambda pl: pl.get("proposed_at") or ""),
        "accepted_plans": sum(1 for pl in plans.values() if pl.get("status") == "accepted"),
        "last_procedure_by_group": last_by_group,
        "recall_due": recall_due,
        "installments_overdue": [i for i in installments.values() if i["status"] == "overdue"],
        "treatment_value": int(value),
        "branches": sorted(branches),
    }
