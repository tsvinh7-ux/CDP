"""Encryption of destination secrets (access tokens, app secrets) at rest."""

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

from .config import settings

_logger = logging.getLogger(__name__)
_warned = False


def _fernet() -> Fernet:
    global _warned
    key = settings.master_key
    if not key:
        if not _warned:
            _logger.warning("CDP_MASTER_KEY is not set: using a key derived from the admin token (dev only)")
            _warned = True
        key = base64.urlsafe_b64encode(hashlib.sha256(("cdp-dev:" + settings.admin_token).encode()).digest())
    return Fernet(key)


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str | None) -> str | None:
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        _logger.error("Cannot decrypt a destination secret (wrong CDP_MASTER_KEY?)")
        return None


def seal(secrets: dict) -> dict:
    return {k: encrypt(v) for k, v in secrets.items() if v}


def mask(config: dict) -> dict:
    """Config as shown in APIs/console: secrets replaced by a marker."""
    out = {k: v for k, v in config.items() if k != "secrets"}
    out["secrets"] = {k: "••••" for k in (config.get("secrets") or {})}
    return out
