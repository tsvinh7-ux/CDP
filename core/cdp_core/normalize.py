"""Identifier normalisation (docs/data-contract-v1.md §6).

Odoo normalises before sending; Core normalises again so that web/Zalo/ads
sources, which cannot be trusted to do it, end up with the same keys.
"""

import re

# Mobile prefixes renumbered from 11 to 10 digits in 2018.
_OLD_MOBILE_PREFIXES = {
    "0120": "070", "0121": "079", "0122": "077", "0126": "076", "0128": "078",
    "0123": "083", "0124": "084", "0125": "085", "0127": "081", "0129": "082",
    "0162": "032", "0163": "033", "0164": "034", "0165": "035", "0166": "036",
    "0167": "037", "0168": "038", "0169": "039",
    "0186": "056", "0188": "058", "0199": "059",
}

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_phone(raw: str | None) -> str | None:
    """Return an E.164 Vietnamese number (+84…) or None if it is not usable."""
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
        # Wrong length, or junk such as 0000000000 / 0999999999.
        return None
    return "+84" + digits[1:]


def normalize_email(raw: str | None) -> str | None:
    if not raw:
        return None
    email = str(raw).strip().lower()
    if not _EMAIL_RE.match(email):
        return None
    local, domain = email.split("@", 1)
    if domain in ("gmail.com", "googlemail.com"):
        local = local.split("+", 1)[0].replace(".", "")
        domain = "gmail.com"
    return f"{local}@{domain}"
