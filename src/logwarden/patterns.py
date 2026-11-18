"""Sensitive-data patterns, in Python, for redaction (`logwarden.logging`) and `scrub()`.

These mirror `adr_sensitive_data` in `_data/policies/logwarden.rego`. tests/test_logging.py
checks that whatever `redact()` leaves passes that rule, so keep the two in sync.
"""
from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"

SENSITIVE_KEY = re.compile(
    r"(?i)(^|[._-])(password|passwd|secret|api[_-]?key|apikey|access[_-]?key|private[_-]?key"
    r"|authorization|cookie|set[_-]?cookie|credentials?|token)$")
_REDACTED_VALUE = re.compile(r"(?i)^(|\*+|\[?redacted\]?|<redacted>|\[filtered\]|\[masked\]|x{3,})$")

_VALUE_PATTERNS = [
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),           # JWT
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"),                                  # Bearer
    re.compile(r"\b(sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,}"
               r"|glpat-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|xox[abprs]-[A-Za-z0-9-]{10,}"
               r"|AIza[0-9A-Za-z_-]{35})"),                                                 # API keys
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),                                      # PEM
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"),         # email
]
# scheme://user:password@host -> keep scheme and host, drop the credentials.
_URL_CREDS = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*://)[^/\s:@]*:[^/\s@]+@")
_CARD = re.compile(r"\b[2-6](?:[ -]?\d){12,18}\b")
# Keys inside a nested JSON string, e.g. '{"password": "x"}'.
_NESTED = re.compile(
    r'(?i)("(?:[a-z0-9]*[._-])?(?:password|passwd|secret|api[_-]?key|apikey|access[_-]?key|private[_-]?key'
    r'|authorization|cookie|credentials?|token)"\s*:\s*)"(?:[^"\\]|\\.)*"')


def luhn_valid(digits: str) -> bool:
    if not 13 <= len(digits) <= 19 or not digits.isdigit():
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
    return total % 10 == 0


def is_sensitive_key(key: str) -> bool:
    return bool(SENSITIVE_KEY.search(key))


def redact_text(text: str) -> str:
    """Replace secrets, credentials, emails and card numbers inside free text."""
    for p in _VALUE_PATTERNS:
        text = p.sub(REDACTED, text)
    text = _URL_CREDS.sub(lambda m: m.group(1) + REDACTED + "@", text)
    text = _CARD.sub(lambda m: REDACTED if luhn_valid(re.sub(r"[ -]", "", m.group(0))) else m.group(0), text)
    return _NESTED.sub(lambda m: m.group(1) + f'"{REDACTED}"', text)


def redact_value(key: str, value: Any, keep: frozenset[str] = frozenset()) -> Any:
    """Redact one field value (recursing into dicts and lists)."""
    if key in keep:
        return value
    if is_sensitive_key(key) and value is not None and not isinstance(value, bool):
        if isinstance(value, str) and _REDACTED_VALUE.match(value):
            return value
        return REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, int) and not isinstance(value, bool) and luhn_valid(str(value)) \
            and str(value)[0] in "23456":
        return REDACTED
    if isinstance(value, dict):
        return {k: redact_value(str(k), v, keep) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_value(key, v, keep) for v in value]
    return value
