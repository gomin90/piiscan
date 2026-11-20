"""Rule based privacy detection engine.

Combines regex patterns, checksums, data statistics and column-name meaning.
Each detector is independent, so a new privacy type only needs a new detector
class registered in `build_detectors`.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from app.config.settings import AnalysisConfig
from app.core.column_stats import ColumnStats
from app.detectors.address import AddressDetector
from app.detectors.base import Detector, profile_of
from app.detectors.bank_account import BankAccountDetector
from app.detectors.birth_date import BirthDateDetector
from app.detectors.credit_card import CreditCardDetector
from app.detectors.custom_identifier import CustomIdentifierDetector
from app.detectors.driver_license import DriverLicenseDetector
from app.detectors.email import EmailDetector
from app.detectors.fingerprint import FingerprintDetector, IrisDetector
from app.detectors.foreigner_id import ForeignerIdDetector
from app.detectors.imei import ImeiDetector
from app.detectors.imsi import ImsiDetector
from app.detectors.insurance_number import InsuranceNumberDetector
from app.detectors.location import LocationDetector
from app.detectors.low_priority import (
    IpAddressDetector,
    MacAddressDetector,
    PostcodeDetector,
    SerialNumberDetector,
    UuidDetector,
)
from app.detectors.passport import PassportDetector
from app.detectors.phone import PhoneDetector
from app.detectors.resident_id import ResidentIdDetector
from app.detectors.social_security import SocialSecurityDetector
from app.detectors.name import NameDetector
from app.utils.masking import mask_value

NULL_TOKENS = {"", "null", "NULL", "n/a", "N/A", "-"}

# Number of matched rows kept per (column, type) for the row-level detail output.
DETAIL_ROW_LIMIT = 20


def hit_rows_for(column: ColumnStats, type_key: str, limit: int = DETAIL_ROW_LIMIT) -> list[dict]:
    """Row numbers of the values that matched, with masked values only."""
    return column.hit_rows.get(type_key, [])[:limit]

# A column is only declared to *be* a privacy column when the matching values
# dominate the column. A detector that fires on a handful of rows of a mostly
# ordinary column (AMOUNT matching the postcode pattern, DEPT matching a Korean
# name pattern) is evidence of a coincidence, not of personal data.
DOMINANT_RATIO = 0.50          # majority of the non-empty values match
HINTED_RATIO = 0.30            # column name also hints at this type
WEAK_RATIO = 0.20              # below this the hit is not reported at all

# Columns whose names say "internal record key" (NO/ID/CD/CODE/SEQ) are not
# personal data just because the values happen to look like an ID format.
IDENTIFIER_NAME_TOKENS = ("no", "id", "cd", "code", "seq", "num", "key", "ref")
GENERIC_CONFIDENCE = 0.85      # below this the pattern alone is not conclusive


def build_detectors(config: AnalysisConfig | None = None) -> list[Detector]:
    config = config or AnalysisConfig()
    detectors: list[Detector] = [
        ResidentIdDetector(),
        PassportDetector(),
        DriverLicenseDetector(),
        ForeignerIdDetector(),
        SocialSecurityDetector(),
        InsuranceNumberDetector(),
        CreditCardDetector(),
        BankAccountDetector(),
        LocationDetector(),
        NameDetector(),
        EmailDetector(),
        PhoneDetector(),
        AddressDetector(),
        BirthDateDetector(),
        ImsiDetector(),
        ImeiDetector(),
        FingerprintDetector(),
        IrisDetector(),
        CustomIdentifierDetector(config.custom_rules.get("custom_rules")),
        SerialNumberDetector(),
        UuidDetector(),
        MacAddressDetector(),
        IpAddressDetector(),
        PostcodeDetector(),
    ]
    by_tag: dict[str, list[Detector]] = {}
    for detector in detectors:
        for tag in detector.candidate_tags:
            by_tag.setdefault(tag, []).append(detector)
    return detectors


class PrivacyEngine:
    def __init__(self, config: AnalysisConfig | None = None):
        self.config = config or AnalysisConfig()
        self.detectors = build_detectors(self.config)
        self.tag_detectors: dict[str, list[Detector]] = {}
        for detector in self.detectors:
            for tag in detector.candidate_tags:
                self.tag_detectors.setdefault(tag, []).append(detector)
        self.elapsed_rules = 0.0
        self.rows_analysed = 0

    def _candidate_detectors(self, tags) -> list[Detector]:
        seen = set()
        out = []
        for tag in tags:
            for detector in self.tag_detectors.get(tag, ()):
                if id(detector) not in seen:
                    seen.add(id(detector))
                    out.append(detector)
        return out

    def process_batch(self, stats: list[ColumnStats], rows: list[list[str]]) -> None:
        started = time.perf_counter()
        first_row = self.rows_analysed + 1
        for offset, row in enumerate(rows, start=first_row):
            for index, value in enumerate(row):
                if index >= len(stats):
                    continue
                column = stats[index]
                profile = profile_of(value)
                column.record(value, profile)
                if "empty" in profile.tags:
                    continue
                for detector in self._candidate_detectors(profile.tags):
                    match = detector.match(value, profile, column.name)
                    if match is not None:
                        column.add_hit(match, offset, mask_value(value))
        self.rows_analysed += len(rows)
        self.elapsed_rules += time.perf_counter() - started

    def finalise(self, stats: list[ColumnStats]) -> list[dict]:
        by_type = {detector.type_key: detector for detector in self.detectors}
        findings = []
        for column in stats:
            affinity = max((d.column_affinity(column.name) for d in self.detectors), default=0.0)
            types: list[dict] = []
            weak: list[dict] = []
            for hit in column.hits.values():
                ratio = hit.count / column.non_empty if column.non_empty else 0.0
                confidence = hit.avg_confidence
                type_affinity = by_type[hit.type_key].column_affinity(column.name) if hit.type_key in by_type else 0.0
                if type_affinity:
                    confidence += type_affinity * 0.2
                if ratio >= 0.8:
                    confidence += 0.08
                if hit.masked_count:
                    confidence += 0.05
                entry = {
                    "type_key": hit.type_key,
                    "display_name": hit.display_name,
                    "hits": hit.count,
                    "masked_hits": hit.masked_count,
                    "ratio": round(ratio, 4),
                    "rule_confidence": round(min(confidence, 1.0), 3),
                    "confirmed": hit.confirmed,
                    "sensitivity": hit.sensitivity,
                    "priority": hit.priority,
                    "evidence": hit.evidence,
                    "column_name_hint": round(type_affinity, 3),
                    "hit_rows": hit_rows_for(column, hit.type_key, self.config.max_hit_rows),
                }
                # Dominance test: a privacy verdict needs the matching values to
                # be the column's dominant content, and that content must be
                # *confirmed* evidence (checksum/strict pattern), not just a
                # superficially similar string.
                lowered_name = column.name.lower()
                identifier_named = any(token in lowered_name for token in IDENTIFIER_NAME_TOKENS)
                looks_like_record_key = identifier_named and not type_affinity
                confirmed_ratio = hit.confirmed_count / column.non_empty if column.non_empty else 0.0
                dominant = ratio >= DOMINANT_RATIO and confirmed_ratio >= DOMINANT_RATIO
                hinted = bool(type_affinity) and ratio >= HINTED_RATIO and hit.confirmed
                if (dominant or hinted) and not (looks_like_record_key and confirmed_ratio < DOMINANT_RATIO):
                    entry["decision"] = "개인정보"
                    types.append(entry)
                elif ratio >= WEAK_RATIO:
                    entry["decision"] = "부분 매칭(검토)"
                    entry["rule_confidence"] = round(min(confidence, 1.0) * min(1.0, ratio + 0.2), 3)
                    weak.append(entry)
            best = max((t["rule_confidence"] for t in types), default=0.0)
            keep = []
            for t in types:
                if t["rule_confidence"] < 0.60 and t["rule_confidence"] < best - 0.15:
                    continue
                keep.append(t)
            types = keep
            types.sort(key=lambda item: (item["priority"], -item["hits"]))
            weak.sort(key=lambda item: (item["priority"], -item["hits"]))
            findings.append(
                {
                    "column": column.name,
                    "index": column.index,
                    "rows": column.rows,
                    "non_empty": column.non_empty,
                    "null": column.null_count,
                    "masked_like": column.masked_count,
                    "distinct_estimate": column.distinct.estimate(),
                    "name_affinity": round(affinity, 3),
                    "types": types,
                    "weak_types": weak,
                    "stats": column.summary(),
                }
            )
        return findings
