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
        elif digits and digits / self.length >= 0.5 and set(v) <= set("0123456789- /.:+"):
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
        if self.length >= 20:
            # Long free text (JSON, log lines, Base64) can hide personal data
            # inside it, so it gets a dedicated tag for interior scanning.
            tags.add("long_text")
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
    # When personal data is found inside a long cell, only the matched fragment
    # is kept (masked) - never the whole cell.
    fragment: str = ""
    priority: int = 1
    sensitivity: float = 0.5
    #: 1순위 / 2순위 from the policy file. Kept apart from confidence and risk.
    tier: int = 0
    legal_category: str = ""
    #: Character offset of this occurrence inside the cell value (0-based).
    match_start: int = -1
    match_end: int = -1
    #: 1-based index of this occurrence within the cell, in left-to-right order.
    occurrence_index: int = 0

    def span(self) -> tuple[int, int]:
        return (self.match_start, self.match_end)

    def as_dict(self) -> dict:
        return {
            "type": self.type_key,
            "display_name": self.display_name,
            "confidence": round(self.confidence, 3),
            "masked": self.masked,
            "confirmed": self.confirmed,
            "evidence": self.evidence,
            "priority": self.priority,
            "tier": self.tier or self.priority,
            "category": self.legal_category,
            "sensitivity": self.sensitivity,
            "match_start": self.match_start,
            "match_end": self.match_end,
            "occurrence": self.occurrence_index,
        }


@dataclass(frozen=True)
class ScanContext:
    """Row/table context a detector may use besides the column name alone.

    `table_name` is the source file (Function A) or the table name (Function B);
    `sibling_names` are the other column names of the same table so a value such
    as "비스포크 오디오" can be read against a GOODS_GROUP column.
    """

    table_name: str = ""
    column_names: tuple[str, ...] = ()
    key_columns: tuple[str, ...] = ()
    sibling_names: tuple[str, ...] = ()

    def mentions(self, *needles: str) -> bool:
        haystack = (self.table_name + " " + " ".join(self.column_names)).upper()
        return any(needle.upper() in haystack for needle in needles)


class Detector:
    """Base class for every privacy detector."""

    type_key: str = "UNKNOWN"
    display_name: str = "알 수 없음"
    priority: int = 2
    sensitivity: float = 0.5
    legal_category: str = ""
    candidate_tags: frozenset[str] = frozenset()
    name_hints: tuple[str, ...] = ()
    # Column names that positively identify the column as something else
    # (amounts, sequences, counters). A detector must not claim a privacy type
    # for such a column on surface form alone.
    negative_hints: tuple[str, ...] = ()

    def _column_semantics(self, column_name: str) -> tuple[float, bool, bool, bool]:
        """(affinity, excluded, identifier-like, device-identifier-like) per column.

        Column names repeat across every cell of a column, so the hint scans are
        done once per (detector, column) instead of once per cell.
        """
        cache = getattr(self, "_column_semantics_cache", None)
        if cache is None:
            cache = {}
            self._column_semantics_cache = cache
        cached = cache.get(column_name)
        if cached is not None:
            return cached
        name = (column_name or "").upper()
        score = 0.0
        for hint in self.name_hints:
            if hint.upper() in name:
                score += 0.35
        excluded = any(hint.upper() in name for hint in self.negative_hints)
        cached = (min(score, 0.8), excluded,
                  any(token in name for token in self.IDENTIFIER_TOKENS),
                  any(token in name for token in self.DEVICE_TOKENS))
        cache[column_name] = cached
        return cached

    def column_affinity(self, column_name: str) -> float:
        """Return 0.0..0.8 based on how strongly the column name hints at this type."""
        return self._column_semantics(column_name)[0]

    def name_excludes(self, column_name: str) -> bool:
        return self._column_semantics(column_name)[1]

    #: Column names that say "this column holds identifiers", not people.
    IDENTIFIER_TOKENS = ("CODE", "REF", "SEQ", "INTERNAL", "SYS", "SKU", "ORDER",
                         "TRACK", "BATCH", "INSTANCE", "TOKEN")
    #: Column names that explain a bare digit run as a device or network
    #: identifier (IMEI/IMSI/MAC/UUID), so a card · account · postcode shape is
    #: not enough evidence on its own.
    DEVICE_TOKENS = ("IMEI", "IMSI", "DEVICE", "TERMINAL", "SUBSCRIBER", "MAC", "UUID")

    def identifier_column(self, column_name: str) -> bool:
        semantics = self._column_semantics(column_name)
        return semantics[1] or semantics[2]

    def device_identifier_column(self, column_name: str) -> bool:
        return self._column_semantics(column_name)[3]

    def contextual_match(self, match: Match, column_name: str, value: str = "") -> Match:
        """Adjust a format match using the column it came from.

        A 15-digit string is an IMEI only if nothing else explains it. When the
        column is named like a system identifier (CODE / REF / ORDER / SEQ) and
        carries no hint of this privacy type, a bare run of digits is not enough
        evidence to confirm personal data, so the match is downgraded to
        "needs review" instead of being silently accepted.

        Values with internal structure (segments, separators, hyphen-separated
        account layouts) keep their verdict: that shape is not what an arbitrary
        internal code looks like.
        """
        if match is None:
            return match
        fragment = (match.fragment or "").strip() or (value or "").strip()
        #: A masked value can never be confirmed by shape alone: the visible part
        #: is a fragment of the real one, so the cell goes to review instead of
        #: being counted as personal data (and is never cleared either).
        if re.search(r"[*＊●■○]{2,}", fragment) and match.type_key != "MASKED":
            match.confirmed = False
            match.confidence = min(match.confidence, 0.60)
            match.masked = True
            match.evidence += " (마스킹된 값 - 형식만 근거, 원문 확인 필요)"
        #: A bare run of digits, or a digit template such as "59************1", has
        #: no structure that distinguishes it from an internal identifier. Values
        #: with real segments ("1234-567890123") keep their verdict.
        if match.confirmed and re.fullmatch(r"[0-9]+|[0-9*＊]{6,}", fragment):
            affinity = self.column_affinity(column_name)
            if ((self.identifier_column(column_name) or self.device_identifier_column(column_name))
                    and affinity == 0.0):
                match.confidence = max(0.1, match.confidence - 0.30)
                match.confirmed = False
                match.evidence += f" (컬럼 {column_name}은 식별자/코드 컬럼 - 근거 부족)"
        return match

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        raise NotImplementedError

    def find_matches(self, value: str, profile: ValueProfile, column_name: str = "",
                    context: ScanContext | None = None) -> list[Match]:
        """All occurrences of this type inside one cell.

        `match()` is the single-hit API the engine used before multi-occurrence
        support; `find_matches()` is the complete one. The default implementation
        reports the single match with its character offsets. Detectors that can
        find several occurrences (phones, e-mails, card numbers, ...) override it
        and must set `match_start` / `match_end` / `occurrence_index`.

        `context` carries the table name and the sibling column names, which is
        what lets a name-like value be read as a person, a product or a branch.
        """
        return self._offset(self.match(value, profile, column_name), value)

    def _offset(self, match: Match | None, value: str) -> list[Match]:
        """Attach character offsets (and the policy tier) to a whole-cell match."""
        if match is None:
            return []
        if not match.tier:
            match.tier = self.priority
        if not match.legal_category:
            match.legal_category = self.legal_category
        if match.match_start < 0:
            text = value or ""
            fragment = match.fragment or text
            start = text.find(fragment) if fragment else 0
            if start < 0:
                start, fragment = 0, text
            match.match_start = start
            match.match_end = start + len(fragment)
            match.occurrence_index = 1
        return [match]

    def applies(self, profile: ValueProfile) -> bool:
        if "empty" in profile.tags:
            return False
        return bool(self.candidate_tags & profile.tags)

    def safe(self) -> bool:
        """Detectors that need at least one matching tag are safe to run always."""
        return True
