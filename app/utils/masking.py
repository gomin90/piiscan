"""Privacy-safe helpers for turning raw values into displayable samples.

Raw personal data must never reach the UI, logs, reports, or the LLM prompt,
so every outward-facing value goes through these helpers.
"""
from __future__ import annotations

import re

MASK_CHARS = {"*", "＊", "X", "x", "•", "○", "□"}

NULL_VALUES = {"", "null", "NULL", "Null", "n/a", "N/A", "na", "NA", "-", "\\", "none", "NONE", "undefined"}


def is_null(value: str) -> bool:
    return value is None or value.strip() in NULL_VALUES


def value_length(value: str) -> int:
    return len(value) if value else 0


def mask_value(value: str, keep_head: int = 2, keep_tail: int = 1) -> str:
    """Return a masked representation of a value safe for logs, UI and reports."""
    if value is None:
        return ""
    text = value.strip()
    if not text:
        return ""
    if "@" in text:
        head, tail = text.split("@", 1)
        return (head[:1] or "*") + "***@***" + (tail[-4:] if len(tail) > 4 else "")
    if len(text) <= 3:
        return text[0] + "**" if len(text) > 1 else "*"
    if len(text) <= 8:
        return text[:2] + "*" * (len(text) - 3) + text[-1]
    return text[:keep_head] + "*" * min(len(text) - keep_head - keep_tail, 12) + text[-keep_tail:]


def looks_masked(value: str) -> bool:
    """True when a value is already redacted (e.g. 010-****-1234, 홍**, abc***)."""
    if not value:
        return False
    masked = sum(1 for ch in value if ch in MASK_CHARS)
    if masked == 0:
        return False
    core = re.sub(r"\d+", "", value)
    # A digit-heavy value with a run of mask characters is a redacted identifier.
    return masked >= 2 or masked >= max(2, len(value) // 4) or ("@" in value and masked >= 3)


def redact_digits(value: str, keep: int = 2) -> str:
    """Keep only the leading/trailing digits of a numeric identifier."""
    return re.sub(r"\d+", lambda m: m.group(0)[:keep] + "*" * max(len(m.group(0)) - keep, 0), value)
