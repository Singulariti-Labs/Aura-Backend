"""Secret detection and redaction helpers for the provider boundary."""

from __future__ import annotations

import re
from collections.abc import Iterable


REDACTED_SECRET = "[REDACTED_SECRET]"

SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.IGNORECASE),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{12,}=*", re.IGNORECASE),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{12,}\b", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAIza[A-Za-z0-9_-]{30,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    re.compile(
        r"\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|secret)"
        r"\s*[:=]\s*['\"]?[A-Za-z0-9._~+/-]{12,}",
        re.IGNORECASE,
    ),
)


def redact_secrets(value: str) -> str:
    """Replace common credentials before episode text leaves the server."""

    redacted = value
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub(REDACTED_SECRET, redacted)
    return redacted


def contains_secret_like_content(values: Iterable[str]) -> bool:
    """Return whether any output string resembles a known credential format."""

    return any(
        pattern.search(value)
        for value in values
        for pattern in SECRET_PATTERNS
    )
