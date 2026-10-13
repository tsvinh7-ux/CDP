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
    if op in ("older_than_minutes", "within_days"):
        dt = parse_dt(v)
        if dt is None:
            return False
        if op == "older_than_minutes":
            return dt <= now - timedelta(minutes=float(target))
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


def default_segments(abandon_minutes: int = 60, transfer_minutes: int = 30) -> list[dict]:
    """Starter segments for an education tenant (DMAG)."""
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
    ]
