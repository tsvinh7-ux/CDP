"""Helpers shared by the CDP add-ons (no ORM state)."""

import base64
import hashlib
import hmac
import re
import time

_OLD_MOBILE_PREFIXES = {
    "0120": "070", "0121": "079", "0122": "077", "0126": "076", "0128": "078",
    "0123": "083", "0124": "084", "0125": "085", "0127": "081", "0129": "082",
    "0162": "032", "0163": "033", "0164": "034", "0165": "035", "0166": "036",
    "0167": "037", "0168": "038", "0169": "039",
    "0186": "056", "0188": "058", "0199": "059",
}


def normalize_phone(raw):
    """E.164 Vietnamese number or None (same rules as CDP Core)."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", str(raw))
    if digits.startswith("84"):
        digits = "0" + digits[2:]
    if not digits.startswith("0"):
        digits = "0" + digits
    if len(digits) == 11 and digits[:4] in _OLD_MOBILE_PREFIXES:
        digits = _OLD_MOBILE_PREFIXES[digits[:4]] + digits[4:]
    if len(digits) != 10 or len(set(digits[1:])) == 1:
        return None
    return "+84" + digits[1:]


def normalize_email(raw):
    if not raw or "@" not in str(raw):
        return None
    return str(raw).strip().lower()


def sign(secret, timestamp, body):
    msg = str(timestamp).encode() + b"." + body
    return "sha256=" + hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def verify(secret, timestamp, signature, body, ttl=300):
    if not (secret and timestamp and signature):
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs(time.time() - ts) > ttl:
        return False
    return hmac.compare_digest(sign(secret, ts, body), signature)


def make_user_token(secret, partner_id, ttl=86400):
    """Signed partner id rendered into the website for the SDK's identify()."""
    exp = int(time.time() + ttl)
    payload = f"{partner_id}.{exp}"
    mac = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    encoded = base64.urlsafe_b64encode(payload.encode()).rstrip(b"=").decode()
    return f"{encoded}.{mac}"
