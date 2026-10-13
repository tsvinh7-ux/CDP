"""Touchpoints: every interaction between a customer and DMAG/clinic, normalised.

A touchpoint = (time, key, channel, stage, direction, label). Sources:
  * events (web SDK, Odoo outbox, Zalo/Facebook webhooks)  – what the customer did,
  * deliveries (ZNS, …) and triggers to sales               – what we did to them.

Channels are ordered: the order is the colour slot used everywhere (dashboard
stacks, journey lane swatches), so a channel keeps its colour across views.
Stages are an ordered scale (customer lifecycle).
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Activation, Delivery, Event, Segment
from .traits import _label, aware, iso

CHANNELS = [  # (key, label) – order = colour slot
    ("web", "Website"),
    ("zalo", "Zalo"),
    ("facebook", "Facebook / quảng cáo"),
    ("email", "Email"),
    ("sales", "Sales / CRM"),
    ("event", "Sự kiện / phòng khám"),
    ("learning", "Học tập / điều trị"),
    ("commerce", "Đơn hàng / thanh toán"),
]
CHANNEL_KEYS = [c for c, _l in CHANNELS]
STAGES = [  # ordered
    ("awareness", "Nhận biết"),
    ("consideration", "Cân nhắc"),
    ("intent", "Ý định"),
    ("purchase", "Mua"),
    ("loyalty", "Sử dụng & gắn bó"),
]
STAGE_INDEX = {s: i for i, (s, _l) in enumerate(STAGES)}

# event_type → (channel, stage, direction). "in" = the customer acted, "out" = we reached out.
EVENT_MAP = {
    "identify": ("web", "consideration", "in"),
    "account.signed_up": ("web", "consideration", "in"),
    "Course Viewed": ("web", "consideration", "in"),
    "Products Searched": ("web", "consideration", "in"),
    "Form Submitted": ("web", "intent", "in"),
    "Product Added": ("commerce", "intent", "in"),
    "Checkout Started": ("commerce", "intent", "in"),
    "cart.updated": ("commerce", "intent", "in"),
    "order.placed": ("commerce", "intent", "in"),
    "order.confirmed": ("commerce", "purchase", "in"),
    "order.paid": ("commerce", "purchase", "in"),
    "order.cancelled": ("commerce", "intent", "in"),
    "Lesson Viewed": ("learning", "loyalty", "in"),
    "Video Opened": ("learning", "loyalty", "in"),
    "Video Progress": ("learning", "loyalty", "in"),
    "Lesson Time Spent": ("learning", "loyalty", "in"),
    "course.enrolled": ("learning", "purchase", "in"),
    "course.progressed": ("learning", "loyalty", "in"),
    "lesson.completed": ("learning", "loyalty", "in"),
    "course.completed": ("learning", "loyalty", "in"),
    "certificate.issued": ("learning", "loyalty", "in"),
    "zalo.follow": ("zalo", "consideration", "in"),
    "zalo.unfollow": ("zalo", "loyalty", "in"),
    "zalo.user_send_text": ("zalo", "intent", "in"),
    "zalo.user_submit_info": ("zalo", "intent", "in"),
    "zalo.user_received_message": ("zalo", "awareness", "out"),
    "zalo.user_seen_message": ("zalo", "consideration", "in"),
    "facebook.lead_submitted": ("facebook", "intent", "in"),
    "marketing.email_sent": ("email", "awareness", "out"),
    "marketing.email_opened": ("email", "consideration", "in"),
    "marketing.email_clicked": ("email", "intent", "in"),
    "marketing.email_replied": ("email", "intent", "in"),
    "marketing.email_bounced": ("email", "awareness", "out"),
    "marketing.unsubscribed": ("email", "loyalty", "in"),
    "lead.created": ("sales", "consideration", "in"),
    "lead.stage_changed": ("sales", "intent", "out"),
    "lead.won": ("sales", "purchase", "out"),
    "lead.lost": ("sales", "intent", "out"),
    "sales.activity_done": ("sales", "intent", "out"),
    "event.registered": ("event", "consideration", "in"),
    "event.attended": ("event", "consideration", "in"),
    "event.cancelled": ("event", "consideration", "in"),
    "appointment.booked": ("event", "intent", "in"),
    "appointment.rescheduled": ("event", "intent", "in"),
    "appointment.cancelled": ("event", "intent", "in"),
    "appointment.checked_in": ("event", "loyalty", "in"),
    "appointment.no_show": ("event", "intent", "in"),
    "treatment_plan.proposed": ("sales", "consideration", "out"),
    "treatment_plan.accepted": ("commerce", "purchase", "in"),
    "treatment_plan.declined": ("sales", "intent", "in"),
    "procedure.completed": ("learning", "loyalty", "in"),
    "installment.due": ("commerce", "loyalty", "out"),
    "installment.paid": ("commerce", "loyalty", "in"),
    "installment.overdue": ("commerce", "loyalty", "in"),
}
AD_SOURCES = {"facebook": "facebook", "fb": "facebook", "ig": "facebook", "instagram": "facebook",
              "zalo": "zalo", "email": "email", "newsletter": "email"}


@dataclass
class Touchpoint:
    at: object
    key: str
    channel: str
    stage: str
    direction: str
    label: str
    source: str

    def as_dict(self) -> dict:
        return {"at": iso(self.at), "key": self.key, "channel": self.channel, "stage": self.stage,
                "direction": self.direction, "label": self.label, "source": self.source}


def classify(ev: Event) -> Touchpoint | None:
    """Map an event to a touchpoint (None for technical events)."""
    if ev.kind in ("entity.upserted", "entity.deleted"):
        return None
    props = (ev.payload or {}).get("properties") or {}
    et = ev.event_type
    if ev.kind == "page" or et == "page":
        src = (props.get("utm_source") or "").lower()
        if src in AD_SOURCES:
            channel = AD_SOURCES[src]
            return Touchpoint(aware(ev.occurred_at), f"{channel}.ad_click", channel, "awareness", "in",
                              f"Đến website từ {src} ({props.get('utm_campaign') or 'chiến dịch'})", ev.source)
        stage = "awareness" if props.get("path") in ("/", None, "") else "consideration"
        return Touchpoint(aware(ev.occurred_at), "web.page", "web", stage, "in", _label(ev), ev.source)
    mapped = EVENT_MAP.get(et)
    if mapped is None:
        return None
    channel, stage, direction = mapped
    return Touchpoint(aware(ev.occurred_at), et, channel, stage, direction, _label(ev), ev.source)


def for_profile(session: Session, tenant_id: str, profile_id: str, limit: int = 500) -> list[Touchpoint]:
    events = session.scalars(select(Event).where(Event.tenant_id == tenant_id, Event.profile_id == profile_id)
                             .order_by(Event.occurred_at.desc(), Event.id.desc()).limit(limit)).all()
    out = [tp for tp in (classify(e) for e in events) if tp]
    # What we did: messages actually sent, and hand-offs to sales.
    for d in session.scalars(select(Delivery).where(Delivery.tenant_id == tenant_id, Delivery.profile_id == profile_id,
                                                    Delivery.status == "sent", Delivery.kind == "zns")).all():
        out.append(Touchpoint(aware(d.sent_at or d.created_at), "zalo.zns_sent", "zalo",
                              "awareness" if d.purpose == "marketing" else "loyalty", "out",
                              f"CDP gửi ZNS ({d.payload.get('segment_key')})", "cdp"))
    names = {s.key: s.name for s in session.scalars(select(Segment).where(Segment.tenant_id == tenant_id))}
    for a in session.scalars(select(Activation).where(Activation.tenant_id == tenant_id,
                                                      Activation.profile_id == profile_id,
                                                      Activation.kind == "trigger")).all():
        out.append(Touchpoint(aware(a.created_at), "sales.cdp_trigger", "sales", "intent", "out",
                              f"CDP báo sales: {names.get(a.segment_key, a.segment_key)}", "cdp"))
    out.sort(key=lambda t: t.at)
    return out


def sequence(touchpoints: list[Touchpoint]) -> list[str]:
    """Touchpoint keys with consecutive repeats collapsed (input of the next-touchpoint model)."""
    seq: list[str] = []
    for tp in touchpoints:
        if not seq or seq[-1] != tp.key:
            seq.append(tp.key)
    return seq


def meta() -> dict:
    return {"channels": [{"key": k, "label": l} for k, l in CHANNELS],
            "stages": [{"key": k, "label": l} for k, l in STAGES]}


def describe(key: str) -> tuple[str, str, str, str]:
    """(channel, stage, direction, label) for a touchpoint key, used to render predictions."""
    if key.endswith(".ad_click"):
        ch = key.split(".")[0]
        return ch, "awareness", "in", f"Click quảng cáo {dict(CHANNELS)[ch]}"
    if key == "web.page":
        return "web", "consideration", "in", "Xem website"
    if key == "zalo.zns_sent":
        return "zalo", "awareness", "out", "Nhận ZNS"
    if key == "sales.cdp_trigger":
        return "sales", "intent", "out", "Sales liên hệ"
    channel, stage, direction = EVENT_MAP.get(key, ("web", "consideration", "in"))
    label = PREDICTION_LABELS.get(key) or key
    return channel, stage, direction, label


PREDICTION_LABELS = {
    "Course Viewed": "Xem khóa học", "Product Added": "Thêm vào giỏ", "Checkout Started": "Bắt đầu thanh toán",
    "cart.updated": "Thêm vào giỏ", "order.placed": "Đặt đơn", "order.paid": "Thanh toán đơn hàng",
    "order.confirmed": "Xác nhận đơn", "course.enrolled": "Ghi danh khóa học", "lesson.completed": "Học tiếp bài mới",
    "course.progressed": "Tiến độ khóa học", "course.completed": "Hoàn thành khóa học",
    "certificate.issued": "Nhận chứng chỉ", "identify": "Đăng nhập website", "account.signed_up": "Tạo tài khoản",
    "zalo.follow": "Quan tâm Zalo OA", "zalo.user_send_text": "Nhắn tin Zalo OA",
    "zalo.user_submit_info": "Chia sẻ thông tin qua Zalo", "zalo.user_seen_message": "Xem tin ZNS",
    "facebook.lead_submitted": "Điền form quảng cáo Facebook", "marketing.email_opened": "Mở email",
    "marketing.email_clicked": "Click email", "marketing.email_sent": "Nhận email",
    "lead.created": "Trở thành lead", "lead.won": "Chốt đơn qua sales", "sales.activity_done": "Sales gọi/gặp",
    "event.registered": "Đăng ký webinar/lớp", "event.attended": "Tham dự webinar/lớp",
    "Video Progress": "Xem video bài học", "Lesson Viewed": "Mở bài học",
    "appointment.booked": "Đặt lịch hẹn", "appointment.checked_in": "Đến khám", "appointment.no_show": "Vắng hẹn",
    "treatment_plan.proposed": "Được tư vấn điều trị", "treatment_plan.accepted": "Chốt kế hoạch điều trị",
    "procedure.completed": "Làm thủ thuật", "installment.paid": "Trả góp",
}
