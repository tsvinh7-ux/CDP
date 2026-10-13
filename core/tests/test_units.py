import time
from datetime import datetime, timedelta, timezone

from cdp_core import security
from cdp_core.normalize import normalize_email, normalize_phone
from cdp_core.segments import evaluate


def test_phone_normalisation():
    assert normalize_phone("0901 234 567") == "+84901234567"
    assert normalize_phone("+84 90.123.4567") == "+84901234567"
    assert normalize_phone("84901234567") == "+84901234567"
    # Old 11-digit Viettel number renumbered in 2018.
    assert normalize_phone("01688123456") == "+84388123456"
    assert normalize_phone("0000000000") is None
    assert normalize_phone("12345") is None


def test_email_normalisation():
    assert normalize_email("  Bs.Lan+khoa1@Gmail.com ") == "bslan@gmail.com"
    assert normalize_email("info@nhakhoa.vn") == "info@nhakhoa.vn"
    assert normalize_email("not-an-email") is None


def test_signature_roundtrip():
    body = b'{"a":1}'
    ts = int(time.time())
    sig = security.sign("s", ts, body)
    assert security.verify("s", str(ts), sig, body, 300)
    assert not security.verify("s", str(ts), sig, b'{"a":2}', 300)
    assert not security.verify("other", str(ts), sig, body, 300)
    assert not security.verify("s", str(ts - 1000), security.sign("s", ts - 1000, body), body, 300)


def test_user_token():
    tok = security.make_user_token("s", 42, ttl=60)
    assert security.read_user_token("s", tok) == 42
    assert security.read_user_token("x", tok) is None
    assert security.read_user_token("s", tok, now=time.time() + 120) is None
    assert security.read_user_token("s", "garbage") is None


def test_rule_evaluation():
    now = datetime.now(timezone.utc)
    traits = {
        "job_role": "clinic_owner",
        "clinic": {"staff_course_tags": ["Cơ bản"]},
        "open_cart": {"updated_at": (now - timedelta(minutes=90)).isoformat()},
        "awaiting_payment": [{"since": (now - timedelta(minutes=5)).isoformat()}],
    }
    rule = {"all": [
        {"trait": "job_role", "op": "eq", "value": "clinic_owner"},
        {"trait": "clinic.staff_course_tags", "op": "contains", "value": "Cơ bản"},
        {"trait": "clinic.staff_course_tags", "op": "not_contains", "value": "Nâng cao"},
    ]}
    assert evaluate(rule, traits, now)
    assert evaluate({"trait": "open_cart.updated_at", "op": "older_than_minutes", "value": 60}, traits, now)
    assert not evaluate({"trait": "awaiting_payment.0.since", "op": "older_than_minutes", "value": 30}, traits, now)
    assert evaluate({"trait": "missing", "op": "not_contains", "value": "x"}, traits, now)
    assert not evaluate({"trait": "missing", "op": "eq", "value": 1}, traits, now)
