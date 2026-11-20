"""Streaming per-column statistics with bounded memory."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from app.detectors.base import ValueProfile
from app.utils.hyperloglog import BoundedSet
from app.utils.masking import is_null, looks_masked, mask_value

LENGTH_BUCKETS = (4, 8, 12, 16, 24, 32, 64, 128)


@dataclass
class Hit:
    type_key: str
    display_name: str
    count: int = 0
    masked_count: int = 0
    confirmed_count: int = 0
    confidence_sum: float = 0.0
    max_confidence: float = 0.0
    evidence: str = ""
    confirmed: bool = True
    sensitivity: float = 0.5
    priority: int = 2

    @property
    def avg_confidence(self) -> float:
        return self.confidence_sum / self.count if self.count else 0.0


@dataclass
class ColumnStats:
    name: str
    index: int
    rows: int = 0
    non_empty: int = 0
    null_count: int = 0
    masked_count: int = 0
    numeric_count: int = 0
    min_len: int = 0
    max_len: int = 0
    total_len: int = 0
    length_buckets: Counter = field(default_factory=Counter)
    tags: Counter = field(default_factory=Counter)
    distinct: BoundedSet = field(default_factory=lambda: BoundedSet(limit=2000))
    hits: dict[str, Hit] = field(default_factory=dict)
    samples: list[str] = field(default_factory=list)
    # Per-type list of the rows that actually matched, so the report can show
    # *which rows* were caught instead of only a column-level count.
    hit_rows: dict[str, list[dict]] = field(default_factory=dict)
    max_hit_rows: int = 50
    min_numeric: float | None = None
    max_numeric: float | None = None

    def record(self, value: str, profile: ValueProfile) -> None:
        self.rows += 1
        if is_null(value):
            self.null_count += 1
            return
        self.non_empty += 1
        stripped = value.strip()
        if looks_masked(stripped):
            self.masked_count += 1
        if profile.digits and profile.digits / max(profile.length, 1) >= 0.6:
            self.numeric_count += 1
        try:
            numeric = float(stripped.replace(",", "").replace("-", ""))
        except ValueError:
            numeric = None
        if numeric is not None:
            if profile.length <= 14:
                self.min_numeric = numeric if self.min_numeric is None else min(self.min_numeric, numeric)
                self.max_numeric = numeric if self.max_numeric is None else max(self.max_numeric, numeric)
        self.total_len += profile.length
        self.min_len = profile.length if self.min_len == 0 else min(self.min_len, profile.length)
        self.max_len = max(self.max_len, profile.length)
        for bucket in LENGTH_BUCKETS:
            if profile.length <= bucket:
                self.length_buckets[bucket] += 1
                break
        else:
            self.length_buckets[">128"] += 1
        for tag in profile.tags:
            self.tags[tag] += 1
        self.distinct.add(stripped)
        if len(self.samples) < 6:
            self.samples.append(mask_value(stripped))

    def add_hit(self, match, row_number: int = 0, masked_value: str = "") -> None:
        hit = self.hits.get(match.type_key)
        if hit is None:
            hit = Hit(
                type_key=match.type_key,
                display_name=match.display_name,
                evidence=match.evidence,
                confirmed=match.confirmed,
                sensitivity=match.sensitivity,
                priority=match.priority,
            )
            self.hits[match.type_key] = hit
        hit.count += 1
        if match.masked:
            hit.masked_count += 1
        if match.confirmed:
            hit.confirmed_count += 1
        hit.confidence_sum += match.confidence
        hit.max_confidence = max(hit.max_confidence, match.confidence)
        hit.confirmed = hit.confirmed and match.confirmed
        if not hit.evidence:
            hit.evidence = match.evidence
        rows = self.hit_rows.setdefault(match.type_key, [])
        if len(rows) < self.max_hit_rows:
            rows.append({"row": row_number, "value": masked_value, "confidence": round(match.confidence, 3)})

    def detection_ratio(self, type_key: str) -> float:
        hit = self.hits.get(type_key)
        if not hit or self.non_empty == 0:
            return 0.0
        return hit.count / self.non_empty

    def summary(self) -> dict:
        return {
            "column": self.name,
            "rows": self.rows,
            "non_empty": self.non_empty,
            "null": self.null_count,
            "masked_like": self.masked_count,
            "distinct_estimate": self.distinct.estimate(),
            "min_len": self.min_len,
            "max_len": self.max_len,
            "avg_len": round(self.total_len / self.non_empty, 2) if self.non_empty else 0,
            "numeric_ratio": round(self.numeric_count / self.non_empty, 3) if self.non_empty else 0,
            "length_buckets": dict(self.length_buckets),
            "tags": dict(self.tags),
            "samples": self.samples,
        }
