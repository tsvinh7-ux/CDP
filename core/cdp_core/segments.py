"""Segment rules: a small JSON DSL evaluated against profile traits.

    {"all": [cond, ...]} | {"any": [cond, ...]} | {"not": cond}
    cond = {"trait": "clinic.staff_course_tags", "op": "contains", "value": "Cơ bản"}
"""

from datetime import datetime, timedelta

from .traits import parse_dt

_MISSING = object()


def get_path(traits: dict, path: str):
    value = traits
    for part in path.split("."):
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return _MISSING
    return value


def _empty(v) -> bool:
    return v is _MISSING or v is None or v == [] or v == {} or v == ""


def _cond(cond: dict, traits: dict, now: datetime) -> bool:
    v = get_path(traits, cond["trait"])
    op, target = cond["op"], cond.get("value")
    if op == "exists":
        return v is not _MISSING and v is not None
    if op == "not_exists":
        return v is _MISSING or v is None
    if op == "empty":
        return _empty(v)
    if op == "not_empty":
        return not _empty(v)
    if op == "not_contains":
        return _empty(v) or target not in v
    if v is _MISSING or v is None:
        return False
    if op == "eq":
        return v == target
    if op == "ne":
        return v != target
    if op == "in":
        return v in target
    if op == "contains":
        return target in v
    if op in ("gt", "gte", "lt", "lte"):
        try:
            v, target = float(v), float(target)
        except (TypeError, ValueError):
            return False
        return {"gt": v > target, "gte": v >= target, "lt": v < target, "lte": v <= target}[op]
    if op in ("older_than_minutes", "within_days", "in_next_days"):
        dt = parse_dt(v)
        if dt is None:
            return False
        if op == "older_than_minutes":
            return dt <= now - timedelta(minutes=float(target))
        if op == "in_next_days":
            return now <= dt <= now + timedelta(days=float(target))
        return dt >= now - timedelta(days=float(target))
    raise ValueError(f"Unknown operator {op!r}")


def evaluate(rule: dict, traits: dict, now: datetime) -> bool:
    if "all" in rule:
        return all(evaluate(r, traits, now) for r in rule["all"])
    if "any" in rule:
        return any(evaluate(r, traits, now) for r in rule["any"])
    if "not" in rule:
        return not evaluate(rule["not"], traits, now)
    return _cond(rule, traits, now)


def referenced_traits(rule: dict) -> list[str]:
    """Top-level trait names a rule reads (used to build trigger payloads)."""
    out: list[str] = []
    for key in ("all", "any"):
        for r in rule.get(key, []):
            out += referenced_traits(r)
    if "not" in rule:
        out += referenced_traits(rule["not"])
    if "trait" in rule:
        out.append(rule["trait"].split(".")[0])
    return list(dict.fromkeys(out))


OPERATORS = {
    "eq": "bằng", "ne": "khác", "gt": ">", "gte": "≥", "lt": "<", "lte": "≤", "in": "thuộc danh sách",
    "contains": "chứa", "not_contains": "không chứa", "exists": "có giá trị", "not_exists": "không có giá trị",
    "empty": "rỗng", "not_empty": "không rỗng", "older_than_minutes": "cũ hơn (phút)",
    "within_days": "trong vòng (ngày qua)", "in_next_days": "trong (ngày tới)",
}

# Traits offered by the segment builder (path, label, kind).
TRAIT_CATALOG = [
    ("job_role", "Vai trò (clinic_owner, dentist, assistant…)", "text"),
    ("rfm_segment", "Nhóm RFM", "text"),
    ("total_spent", "Tổng chi tiêu (đ)", "number"),
    ("orders_count", "Số đơn đã thanh toán", "number"),
    ("lead_score", "Điểm lead (0–100)", "number"),
    ("engagement_score", "Điểm tương tác (0–100)", "number"),
    ("last_seen_at", "Lần hoạt động gần nhất", "date"),
    ("last_order_at", "Lần mua gần nhất", "date"),
    ("course_tags_enrolled", "Thẻ khóa đã học", "list"),
    ("hot_courses", "Khóa đang quan tâm", "list"),
    ("stalled_courses", "Khóa học dở", "list"),
    ("open_cart", "Giỏ hàng chưa thanh toán", "object"),
    ("open_cart.updated_at", "Giỏ hàng cập nhật lúc", "date"),
    ("awaiting_payment", "Đơn chờ thanh toán", "list"),
    ("first_touch.source", "Nguồn đầu tiên (utm_source)", "text"),
    ("first_touch.campaign", "Chiến dịch đầu tiên", "text"),
    ("zalo_follower", "Đang quan tâm Zalo OA", "bool"),
    ("consent.marketing", "Đồng ý nhận tin tiếp thị", "bool"),
    ("consent.ads", "Đồng ý quảng cáo", "bool"),
    ("clinic.name", "Tên phòng khám", "text"),
    ("clinic.staff_count", "Số nhân sự phòng khám", "number"),
    ("clinic.learners_count", "Số nhân sự đã học", "number"),
    ("clinic.staff_course_tags", "Thẻ khóa nhân sự đã học", "list"),
    ("next_best_course.name", "Khóa gợi ý tiếp theo", "text"),
    ("clinic_next_course.name", "Khóa gợi ý cho phòng khám", "text"),
    ("next_appointment_at", "Lịch hẹn sắp tới (nha khoa)", "date"),
    ("last_visit_at", "Lần khám gần nhất (nha khoa)", "date"),
    ("no_show_rate", "Tỷ lệ không đến (nha khoa)", "number"),
    ("open_treatment_plans", "Kế hoạch điều trị chưa chốt", "list"),
    ("recall_due", "Đến hạn tái khám", "list"),
    ("installments_overdue", "Trả góp quá hạn", "list"),
    ("treatment_value", "Giá trị điều trị (đ)", "number"),
]


def dental_segments() -> list[dict]:
    """Starter segments for a dental clinic tenant (Dental ERP add-on)."""
    return [
        {"key": "recall_due", "name": "Đến hạn tái khám định kỳ",
         "description": "Cạo vôi 6 tháng, tái khám niềng, kiểm tra implant… chưa có lịch hẹn.",
         "rule": {"trait": "recall_due", "op": "not_empty"}, "trigger_on_enter": True},
        {"key": "no_show_risk", "name": "Có lịch hẹn 2 ngày tới, hay vắng mặt",
         "description": "Gọi xác nhận trước để giảm ghế trống.",
         "rule": {"all": [{"trait": "next_appointment_at", "op": "in_next_days", "value": 2},
                          {"trait": "no_show_rate", "op": "gte", "value": 0.3}]}, "trigger_on_enter": True},
        {"key": "plan_not_accepted", "name": "Kế hoạch điều trị chưa chốt > 3 ngày",
         "rule": {"all": [{"trait": "open_treatment_plans", "op": "not_empty"},
                          {"trait": "open_treatment_plans.0.proposed_at", "op": "older_than_minutes", "value": 4320}]},
         "trigger_on_enter": True},
        {"key": "installment_overdue", "name": "Trả góp quá hạn",
         "rule": {"trait": "installments_overdue", "op": "not_empty"}, "trigger_on_enter": True},
        {"key": "lapsed_patients", "name": "Không quay lại > 12 tháng",
         "rule": {"trait": "last_visit_at", "op": "older_than_minutes", "value": 525600}},
        {"key": "high_value_patients", "name": "Bệnh nhân giá trị cao (≥ 50 triệu)",
         "rule": {"trait": "treatment_value", "op": "gte", "value": 50_000_000}},
    ]


def default_segments(abandon_minutes: int = 60, transfer_minutes: int = 30, vertical: str = "education") -> list[dict]:
    """Starter segments for a tenant."""
    if vertical == "dental":
        return dental_segments()
    return [
        {
            "key": "clinic_owners",
            "name": "Chủ phòng khám",
            "description": "Đối tượng chính của Marketing Automation.",
            "rule": {"trait": "job_role", "op": "eq", "value": "clinic_owner"},
        },
        {
            "key": "owner_staff_basic_no_advanced",
            "name": "Chủ PK: nhân sự đã học Cơ bản, chưa học Nâng cao",
            "description": "Phòng khám có nhân sự học khóa Cơ bản nhưng chưa ai học Nâng cao → chào gói nâng cao/doanh nghiệp cho chủ phòng khám.",
            "rule": {"all": [
                {"trait": "job_role", "op": "eq", "value": "clinic_owner"},
                {"trait": "clinic.staff_course_tags", "op": "contains", "value": "Cơ bản"},
                {"trait": "clinic.staff_course_tags", "op": "not_contains", "value": "Nâng cao"},
            ]},
            "trigger_on_enter": True,
        },
        {
            "key": "checkout_abandoned",
            "name": f"Bỏ giỏ hàng > {abandon_minutes} phút",
            "description": "Có giỏ hàng chưa đặt/thanh toán.",
            "rule": {"all": [
                {"trait": "open_cart", "op": "not_empty"},
                {"trait": "open_cart.updated_at", "op": "older_than_minutes", "value": abandon_minutes},
            ]},
            "trigger_on_enter": True,
        },
        {
            "key": "awaiting_transfer",
            "name": f"Đặt đơn, chưa thanh toán > {transfer_minutes} phút",
            "description": "Đơn đã đặt nhưng chưa nhận được tiền (VNPay bỏ dở / chờ chuyển khoản SePay).",
            "rule": {"all": [
                {"trait": "awaiting_payment", "op": "not_empty"},
                {"trait": "awaiting_payment.0.since", "op": "older_than_minutes", "value": transfer_minutes},
            ]},
            "trigger_on_enter": True,
        },
        {
            "key": "hot_leads",
            "name": "Quan tâm cao: xem 1 khóa ≥ 3 lần/14 ngày, chưa mua",
            "description": "Gợi ý cho sales gọi tư vấn.",
            "rule": {"trait": "hot_courses", "op": "not_empty"},
            "trigger_on_enter": True,
        },
        {
            "key": "stalled_learners",
            "name": "Học dở > 14 ngày",
            "rule": {"trait": "stalled_courses", "op": "not_empty"},
        },
        {
            "key": "high_value",
            "name": "Chi tiêu ≥ 10 triệu",
            "rule": {"trait": "total_spent", "op": "gte", "value": 10_000_000},
        },
        {
            "key": "high_lead_score",
            "name": "Điểm lead ≥ 60",
            "description": "Ưu tiên sales gọi trong ngày.",
            "rule": {"trait": "lead_score", "op": "gte", "value": 60},
            "trigger_on_enter": True,
        },
    ]
