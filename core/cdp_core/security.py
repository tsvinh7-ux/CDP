"""Request signing shared with the Odoo add-on (see docs/data-contract-v1.md §4)."""

import base64
import hashlib
import hmac
import time


def sign(secret: str, timestamp: int | str, body: bytes) -> str:
    msg = str(timestamp).encode() + b"." + body
    return "sha256=" + hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def verify(secret: str, timestamp: str | None, signature: str | None, body: bytes, ttl: int) -> bool:
    if not timestamp or not signature:
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs(time.time() - ts) > ttl:
        return False
    return hmac.compare_digest(sign(secret, ts, body), signature)


def signed_headers(tenant_id: str, secret: str, body: bytes) -> dict:
    ts = int(time.time())
    return {
        "Content-Type": "application/json",
        "X-CDP-Tenant": tenant_id,
        "X-CDP-Timestamp": str(ts),
        "X-CDP-Signature": sign(secret, ts, body),
    }


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def make_user_token(secret: str, partner_id: int, ttl: int = 86400, now: float | None = None) -> str:
    """Token Odoo renders into the website so the SDK can call identify()."""
    exp = int((now or time.time()) + ttl)
    payload = f"{partner_id}.{exp}"
    mac = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{_b64(payload.encode())}.{mac}"


def read_user_token(secret: str, token: str | None, now: float | None = None) -> int | None:
    """Return the partner id if the token is authentic and not expired."""
    if not token or "." not in token:
        return None
    encoded, mac = token.rsplit(".", 1)
    try:
        payload = _unb64(encoded).decode()
        partner_s, exp_s = payload.split(".")
        partner_id, exp = int(partner_s), int(exp_s)
    except (ValueError, UnicodeDecodeError):
        return None
    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, mac):
        return None
    if exp < (now or time.time()):
        return None
    return partner_id
