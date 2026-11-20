"""Shared detector primitives.

Every privacy detector is an independent class implementing `Detector`, so new
privacy types can be added without changing the analysis engine.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Iterable

from app.utils.masking import MASK_CHARS, looks_masked

HANGUL_RE = re.compile(r"[\uAC00-\uD7A3\u3130-\u313F]")
DIGIT_RE = re.compile(r"\d")
LATIN_RE = re.compile(r"[A-Za-z]")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
MAC_RE = re.compile(r"^([0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}$")
IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
HEX_RE = re.compile(r"^[0-9a-fA-F]{16,64}$")
BASE64_RE = re.compile(r"^[A-Za-z0-9+/]{16,}={0,2}$")
DECIMAL_RE = re.compile(r"^-?\d{1,3}\.\d{3,}$")


class ValueProfile:
    """Cheap per-value character profile used to gate expensive regex checks."""

    __slots__ = (
        "value",
        "length",
        "digits",
        "alpha",
        "hangul",
        "other",
        "tags",
    )

    def __init__(self, value: str):
        self.value = value
        v = value.strip()
        self.length = len(v)
        if self.length == 0:
            self.digits = self.alpha = self.hangul = self.other = 0
            self.tags = frozenset({"empty"})
            return
        digits = 0
        alpha = 0
        hangul = 0
        other = 0
        for ch in v:
            if "0" <= ch <= "9":
                digits += 1
            elif "A" <= ch <= "Z" or "a" <= ch <= "z":
                alpha += 1
            elif HANGUL_RE.match(ch):
                hangul += 1
            else:
                other += 1
        self.digits = digits
        self.alpha = alpha
        self.hangul = hangul
        self.other = other

        tags: set[str] = set()
        if digits == self.length:
            tags.add("digits")
        elif digits and digits / self.length >= 0.5 and set(v) <= set("0123456789- /.:"):
            tags.add("digits_sep")
        if "@" in v:
            tags.add("email")
        if alpha and digits:
            tags.add("alnum")
        if HANGUL_RE.search(v):
            tags.add("hangul")
        elif alpha and alpha / self.length >= 0.6:
            tags.add("latin")
        if any(ch in MASK_CHARS for ch in v):
            tags.add("masked")
        if HEX_RE.match(v):
            tags.add("hex")
        if BASE64_RE.match(v) and self.length >= 24 and digits + alpha >= self.length - 2:
            tags.add("blob")
        if DECIMAL_RE.match(v):
            tags.add("decimal")
        if UUID_RE.match(v):
            tags.add("uuid")
        if MAC_RE.match(v):
            tags.add("mac")
        if IPV4_RE.match(v):
            tags.add("ip")
        if "." in v and self.length <= 24:
            tags.add("dot_token")
        if "-" in v or " " in v:
            tags.add("separated")
        self.tags = frozenset(tags)

    @property
    def is_all_digits(self) -> bool:
        return "digits" in self.tags

    @property
    def digit_ratio(self) -> float:
        return self.digits / self.length if self.length else 0.0


EMPTY_PROFILE = ValueProfile("")


@lru_cache(maxsize=8192)
def _cached_profile(value: str) -> ValueProfile:
    return ValueProfile(value)


def profile_of(value: str) -> ValueProfile:
    if not value:
        return EMPTY_PROFILE
    return _cached_profile(value)


@dataclass
class Match:
    type_key: str
    display_name: str
    confidence: float
    masked: bool = False
    evidence: str = ""
    confirmed: bool = True
    priority: int = 1
    sensitivity: float = 0.5

    def as_dict(self) -> dict:
        return {
            "type": self.type_key,
            "display_name": self.display_name,
            "confidence": round(self.confidence, 3),
            "masked": self.masked,
            "confirmed": self.confirmed,
            "evidence": self.evidence,
            "priority": self.priority,
            "sensitivity": self.sensitivity,
        }


class Detector:
    """Base class for every privacy detector."""

    type_key: str = "UNKNOWN"
    display_name: str = "알 수 없음"
    priority: int = 2
    sensitivity: float = 0.5
    candidate_tags: frozenset[str] = frozenset()
    name_hints: tuple[str, ...] = ()
    # Column names that positively identify the column as something else
    # (amounts, sequences, counters). A detector must not claim a privacy type
    # for such a column on surface form alone.
    negative_hints: tuple[str, ...] = ()

    def column_affinity(self, column_name: str) -> float:
        """Return 0.0..0.8 based on how strongly the column name hints at this type."""
        name = column_name.upper()
        score = 0.0
        for hint in self.name_hints:
            if hint.upper() in name:
                score += 0.35
        return min(score, 0.8)

    def name_excludes(self, column_name: str) -> bool:
        return any(hint.upper() in column_name.upper() for hint in self.negative_hints)

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        raise NotImplementedError

    def applies(self, profile: ValueProfile) -> bool:
        if "empty" in profile.tags:
            return False
        return bool(self.candidate_tags & profile.tags)

    def safe(self) -> bool:
        """Detectors that need at least one matching tag are safe to run always."""
        return True
