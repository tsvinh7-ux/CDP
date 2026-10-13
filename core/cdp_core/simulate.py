"""Synthetic tenant for demos and model tests (clearly labelled: NOT real customers).

    python -m cdp_core.simulate --tenant dmag_sim --people 1200 --days 120

Generates dental professionals working in clinics who discover DMAG through
Facebook/Zalo/email/organic, browse, follow the Zalo OA, open emails, attend
webinars, talk to sales, buy courses (VNPay/SePay), learn, and later buy the
next level. Behaviour has real structure (topic interest, level progression,
colleague influence, abandonment) so the prediction models have something to
learn – and their measured accuracy is meaningful for this synthetic world only.
"""

import argparse
import random
import uuid
from datetime import datetime, timedelta

from .db import init_db, session_scope
from .jobs import create_tenant, refresh_tenant
from .models import Tenant, utcnow
from .pipeline import ingest_odoo_batch, ingest_web_batch, ingest_zalo
from .security import make_user_token

COURSES = [  # id, name, tags, price
    (1, "Implant Cơ bản", ["Cơ bản", "Implant"], 3_500_000),
    (2, "Implant Nâng cao", ["Nâng cao", "Implant"], 12_000_000),
    (3, "Chỉnh nha Cơ bản", ["Cơ bản", "Chỉnh nha"], 4_200_000),
    (4, "Chỉnh nha Nâng cao", ["Nâng cao", "Chỉnh nha"], 15_000_000),
    (5, "Nội nha Cơ bản", ["Cơ bản", "Nội nha"], 2_800_000),
    (6, "Nội nha Nâng cao", ["Nâng cao", "Nội nha"], 8_500_000),
    (7, "Thẩm mỹ răng sứ", ["Nâng cao", "Thẩm mỹ"], 9_800_000),
    (8, "Kiểm soát nhiễm khuẩn", ["Cơ bản", "Phòng khám"], 1_500_000),
]
TOPICS = {"Implant": (1, 2), "Chỉnh nha": (3, 4), "Nội nha": (5, 6), "Thẩm mỹ": (8, 7), "Phòng khám": (8, 8)}
TOPIC_WEIGHTS = [("Implant", 35), ("Chỉnh nha", 25), ("Nội nha", 20), ("Thẩm mỹ", 12), ("Phòng khám", 8)]
CITIES = ["TP. Hồ Chí Minh", "Hà Nội", "Đà Nẵng", "Cần Thơ", "Hải Phòng", "Nha Trang", "Biên Hòa"]
LAST = ["Nguyễn", "Trần", "Lê", "Phạm", "Hoàng", "Phan", "Vũ", "Đặng", "Bùi", "Đỗ"]
FIRST = ["An", "Bình", "Châu", "Dũng", "Giang", "Hà", "Hải", "Hạnh", "Hiếu", "Hoa", "Hùng", "Khánh", "Lan",
         "Linh", "Long", "Mai", "Minh", "Nam", "Ngọc", "Phong", "Quân", "Thảo", "Thu", "Trang", "Tuấn", "Vy"]
ROLE_PREFIX = {"clinic_owner": "BS.", "dentist": "BS.", "assistant": "", "technician": "KTV.", "student": "SV."}


class Person:
    def __init__(self, rng, pid, clinic, role, t0):
        self.id, self.clinic, self.role = pid, clinic, role
        self.name = f"{ROLE_PREFIX[role]} {rng.choice(LAST)} {rng.choice(FIRST)}".strip()
        self.email = f"sim{pid}@nhakhoa-mo-phong.vn"
        self.phone = f"09{rng.randint(10000000, 99999999)}"
        self.anon = f"sim-anon-{pid}"
        self.t = t0
        self.topic = rng.choices([t for t, _w in TOPIC_WEIGHTS], [w for _t, w in TOPIC_WEIGHTS])[0]
        self.odoo, self.web, self.zalo = [], [], []
        self.known = False
        self.owned: set[int] = set()


def _msg(kind, event_type, at, props=None, subject=None, obj=None):
    m = {"spec_version": "1.0", "event_id": str(uuid.uuid4()), "kind": kind, "event_type": event_type,
         "occurred_at": at.isoformat(), "properties": props or {}}
    if subject:
        m["subject"] = {"model": "res.partner", "id": subject}
    if obj:
        m["object"] = obj
    return m


def simulate(tenant_id: str = "dmag_sim", people: int = 1200, days: int = 120, seed: int = 42) -> dict:
    rng = random.Random(seed)
    now = utcnow()
    start = now - timedelta(days=days)
    course = {c[0]: c for c in COURSES}

    with session_scope() as s:
        tenant = s.get(Tenant, tenant_id) or create_tenant(
            s, tenant_id, "DMAG – dữ liệu MÔ PHỎNG", "sim-secret", f"wk_{tenant_id}", None,
            {"abandon_minutes": 60, "transfer_minutes": 30, "simulated": True})
        catalog = [_msg("entity.upserted", "entity.upserted", start, {"fields": {
            "name": name, "tag_ids_names": tags, "is_published": True}}, obj={"model": "slide.channel", "id": cid})
            for cid, name, tags, _p in COURSES]
        ingest_odoo_batch(s, tenant, catalog)

    clinics = max(1, people // 5)
    persons: list[Person] = []
    clinic_buys: dict[int, set] = {c: set() for c in range(clinics)}
    pid = 0
    for c in range(clinics):
        size = rng.randint(3, 7)
        for k in range(size):
            pid += 1
            role = "clinic_owner" if k == 0 else rng.choices(
                ["dentist", "assistant", "technician", "student"], [50, 38, 7, 5])[0]
            persons.append(Person(rng, pid, c, role, start + timedelta(days=rng.uniform(0, days - 1))))
            if pid >= people:
                break
        if pid >= people:
            break

    def at(p, lo_h, hi_h):
        p.t = p.t + timedelta(hours=rng.uniform(lo_h, hi_h))  # may pass `now`: dropped before ingest
        return p.t

    def web(p, typ, event=None, props=None):
        item = {"type": typ, "anonymousId": p.anon, "messageId": str(uuid.uuid4()),
                "timestamp": p.t.isoformat(), "properties": props or {}}
        if event:
            item["event"] = event
        if p.known:
            item["userToken"] = make_user_token("sim-secret", p.id, ttl=10 ** 9)
        p.web.append(item)

    def sign_up(p):
        if p.known:
            return
        p.known = True
        p.odoo.append(_msg("entity.upserted", "entity.upserted", p.t, {"fields": {
            "name": p.name, "email": p.email, "mobile": p.phone, "dental_role": p.role,
            "parent_id": 100000 + p.clinic, "city": rng.choice(CITIES)}}, obj={"model": "res.partner", "id": p.id}))
        web(p, "identify")

    def view(p, cid, n=1):
        for _ in range(n):
            at(p, 2, 60)
            web(p, "page", props={"path": f"/slides/{cid}", "title": course[cid][1]})
            web(p, "track", "Course Viewed", {"course_id": cid, "course_name": course[cid][1]})

    def buy(p, cid):
        name, price = course[cid][1], course[cid][3]
        at(p, 0.2, 30)
        items = [{"product_id": 1000 + cid, "name": name, "qty": 1, "price": price}]
        oid = rng.randint(10 ** 6, 10 ** 7)
        p.odoo.append(_msg("event", "cart.updated", p.t, {"order_id": oid, "amount": price, "items": items}, p.id))
        if rng.random() < 0.25 + 0.1 * (cid in clinic_buys[p.clinic]):
            return False  # abandoned cart
        at(p, 0.05, 6)
        provider = "vnpay" if rng.random() < 0.6 else "sepay"
        p.odoo.append(_msg("event", "order.placed", p.t, {"order_id": oid, "amount": price, "items": items,
                                                          "provider": provider}, p.id))
        if rng.random() < 0.15:
            return False  # never paid (VNPay abandoned / transfer not sent)
        at(p, 0.05, 30 if provider == "sepay" else 0.5)
        p.odoo.append(_msg("event", "order.paid", p.t, {"order_id": oid, "amount": price, "items": items,
                                                        "provider": provider}, p.id))
        tags = course[cid][2]
        p.odoo.append(_msg("event", "course.enrolled", p.t, {"course_id": cid, "course_name": name,
                                                             "course_tags": tags, "enroll_type": "paid"}, p.id))
        p.owned.add(cid)
        clinic_buys[p.clinic].add(cid)
        lessons = rng.randint(1, 6)
        for k in range(lessons):
            at(p, 10, 96)
            p.odoo.append(_msg("event", "lesson.completed", p.t, {"course_id": cid, "course_name": name,
                                                                  "lesson_id": cid * 100 + k}, p.id))
        if lessons >= 4 and rng.random() < 0.7:
            p.odoo.append(_msg("event", "course.completed", p.t, {"course_id": cid, "course_name": name,
                                                                  "course_tags": tags}, p.id))
        return True

    for p in persons:
        # ---- acquisition
        channel = rng.choices(["facebook", "zalo", "organic", "email"], [40, 20, 25, 15])[0]
        basic, advanced = TOPICS[p.topic]
        utm = {"facebook": ("facebook", "cpc"), "zalo": ("zalo", "oa"), "email": ("email", "newsletter")}.get(channel)
        props = {"path": f"/slides/{basic}", "title": course[basic][1]}
        if utm:
            props.update(utm_source=utm[0], utm_medium=utm[1], utm_campaign=f"{p.topic.lower()}_{utm[1]}")
        web(p, "page", props=props)
        interest = rng.random()
        view(p, basic, 1 + int(interest * 4))
        if rng.random() < 0.25 + 0.5 * interest:
            at(p, 1, 48)
            sign_up(p)
        if not p.known:
            continue
        engaged = 0.0
        if rng.random() < 0.35:  # Zalo OA
            at(p, 1, 72)
            p.zalo.append({"event_name": "follow", "timestamp": str(int(p.t.timestamp() * 1000)),
                           "follower": {"id": f"z{p.id}"}})
            if rng.random() < 0.6:
                p.zalo.append({"event_name": "user_submit_info", "timestamp": str(int(p.t.timestamp() * 1000) + 1),
                               "sender": {"id": f"z{p.id}"}, "info": {"phone": p.phone}})
            engaged += 0.1
        for _k in range(rng.randint(0, 3)):  # newsletter
            at(p, 24, 240)
            camp = rng.choice(["Ưu đãi tháng", "Webinar Implant", "Case lâm sàng"])
            p.odoo.append(_msg("event", "marketing.email_sent", p.t, {"campaign": camp}, p.id))
            if rng.random() < 0.4:
                at(p, 1, 30)
                p.odoo.append(_msg("event", "marketing.email_opened", p.t, {"campaign": camp}, p.id))
                if rng.random() < 0.35:
                    p.odoo.append(_msg("event", "marketing.email_clicked", p.t, {"campaign": camp}, p.id))
                    view(p, basic if basic not in p.owned else advanced, 1)
                    engaged += 0.1
        if rng.random() < 0.25:  # webinar
            at(p, 24, 200)
            ev_name = f"Webinar {p.topic}"
            p.odoo.append(_msg("event", "event.registered", p.t, {"event_id": hash(p.topic) % 1000,
                                                                  "event_name": ev_name}, p.id))
            if rng.random() < 0.6:
                at(p, 24, 120)
                p.odoo.append(_msg("event", "event.attended", p.t, {"event_name": ev_name}, p.id))
                engaged += 0.2
        if rng.random() < 0.3 + 0.2 * (p.role == "clinic_owner"):  # sales
            at(p, 1, 72)
            p.odoo.append(_msg("event", "lead.created", p.t, {"source": channel}, p.id))
            at(p, 2, 48)
            p.odoo.append(_msg("event", "sales.activity_done", p.t, {"activity_type": "Gọi điện",
                                                                     "summary": f"Tư vấn {course[basic][1]}"}, p.id))
            engaged += 0.15
        # ---- first purchase
        p_buy = 0.15 + 0.45 * interest + engaged + 0.15 * (basic in clinic_buys[p.clinic])
        if rng.random() < min(0.9, p_buy):
            if buy(p, basic):
                # ---- progression: the next level of the same topic
                p_next = 0.25 + 0.3 * interest + 0.2 * (p.role in ("clinic_owner", "dentist")) \
                    + 0.15 * (advanced in clinic_buys[p.clinic])
                if advanced != basic and rng.random() < p_next:
                    at(p, 72, 900)
                    view(p, advanced, rng.randint(1, 4))
                    if rng.random() < 0.75:
                        buy(p, advanced)
        # ---- cross-sell: colleague influence or a second topic
        if rng.random() < 0.12:
            other = rng.choice([c for c in clinic_buys[p.clinic] if c not in p.owned] or [8])
            at(p, 48, 600)
            view(p, other, rng.randint(1, 3))
            if rng.random() < 0.5:
                buy(p, other)

    # Journeys that run past "now" are cut off there (no future events, no pile-up on today).
    for p in persons:
        p.odoo = [m for m in p.odoo if datetime.fromisoformat(m["occurred_at"]) <= now]
        p.web = [m for m in p.web if datetime.fromisoformat(m["timestamp"]) <= now]
        p.zalo = [m for m in p.zalo if int(m["timestamp"]) <= now.timestamp() * 1000]
    stats = {"people": len(persons), "known": sum(p.known for p in persons)}
    with session_scope() as s:
        tenant = s.get(Tenant, tenant_id)
        clinics_msgs = [_msg("entity.upserted", "entity.upserted", start, {"fields": {
            "name": f"Nha khoa mô phỏng {c + 1}", "is_company": True, "city": rng.choice(CITIES)}},
            obj={"model": "res.partner", "id": 100000 + c}) for c in range(clinics)]
        ingest_odoo_batch(s, tenant, clinics_msgs)
    events = 0
    for i in range(0, len(persons), 25):
        with session_scope() as s:
            tenant = s.get(Tenant, tenant_id)
            for p in persons[i:i + 25]:
                if p.odoo:
                    ingest_odoo_batch(s, tenant, sorted(p.odoo, key=lambda m: m["occurred_at"]))
                for j in range(0, len(p.web), 100):
                    ingest_web_batch(s, tenant, p.web[j:j + 100])
                for z in p.zalo:
                    ingest_zalo(s, tenant, "OA_SIM", z)
                events += len(p.odoo) + len(p.web) + len(p.zalo)
    stats["events"] = events
    with session_scope() as s:
        tenant = s.get(Tenant, tenant_id)
        from . import predict

        stats["models"] = predict.train(s, tenant)
        refresh_tenant(s, tenant)
    return stats


def main():
    parser = argparse.ArgumentParser(description="Generate a SYNTHETIC CDP tenant")
    parser.add_argument("--tenant", default="dmag_sim")
    parser.add_argument("--people", type=int, default=1200)
    parser.add_argument("--days", type=int, default=120)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    init_db()
    print(simulate(args.tenant, args.people, args.days, args.seed))


if __name__ == "__main__":
    main()
