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
from app.core.row_analysis import (
    IGNORE_VERDICT,
    CellRecord,
    RowRecord,
    analyse_row,
    cell_decision,
)
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
        # Cell/row bookkeeping. Every non-empty cell is inspected by the rule
        # engine; the counts below make "cells checked" and "AI decisions"
        # separately auditable.
        self.rows_inspected = 0
        self.cells_inspected = 0
        self.empty_cells = 0
        self.detector_calls = 0
        # row number -> every detector hit in that row, in column order.
        self.row_cells: dict[int, list[CellRecord]] = {}
        self.row_widths: dict[int, int] = {}
        self.column_cells: dict[int, list[CellRecord]] = {}
        self.rows_with_hits = 0
        self.last_hit_row = 0
        self.last_truncated_row = 0
        self.cell_hits = 0
        self.pii_cells = 0
        self.review_cells = 0
        self.ignored_cells = 0
        self.detail_truncated_rows = 0
        self._tag_cache: dict[frozenset[str], tuple[Detector, ...]] = {}

    def _detectors_for(self, tags: frozenset[str]) -> tuple[Detector, ...]:
        """Detectors that could apply to a value, cached per character profile."""
        cached = self._tag_cache.get(tags)
        if cached is not None:
            return cached
        seen: set[int] = set()
        out: list[Detector] = []
        for tag in tags:
            for detector in self.tag_detectors.get(tag, ()):
                if id(detector) not in seen:
                    seen.add(id(detector))
                    out.append(detector)
        result = tuple(out)
        self._tag_cache[tags] = result
        return result

    def _record_cell(self, row: int, index: int, column: ColumnStats, match, masked: str,
                     detector: Detector, width: int) -> None:
        """Judge one cell hit and, if it is worth reporting, add it to the ledger.

        The verdict comes from the cell's own evidence first; how dominant the
        pattern is in the column and whether the column name hints at that type
        only adjust confidence. A column that is not classified as personal data
        can never cancel a cell hit.
        """
        hit = column.hits.get(match.type_key)
        support = hit.count / column.non_empty if hit and column.non_empty else 0.0
        hint = detector.column_affinity(column.name)
        decision, confidence, needs_review = cell_decision(match.confidence, match.confirmed, support, hint)

        if decision == IGNORE_VERDICT:
            self.ignored_cells += 1
            return

        if decision == "개인정보":
            self.pii_cells += 1
        else:
            self.review_cells += 1
        self.cell_hits += 1

        cells = self.row_cells.get(row)
        if row != self.last_hit_row:
            self.last_hit_row = row
            self.rows_with_hits += 1
        if cells is None:
            if len(self.row_cells) >= self.config.max_detail_rows:
                if row != self.last_truncated_row:
                    self.last_truncated_row = row
                    self.detail_truncated_rows += 1
                return
            cells = []
            self.row_cells[row] = cells
            self.row_widths[row] = width
        cell = CellRecord(
            row=row,
            column_index=index,
            column=column.name,
            type_key=match.type_key,
            display_name=match.display_name,
            confidence=round(confidence, 3),
            masked_value=masked,
            evidence=match.evidence,
            confirmed=match.confirmed,
            masked=match.masked,
            sensitivity=match.sensitivity,
            priority=match.priority,
            decision=decision,
            needs_review=needs_review,
            column_support=round(support, 4),
        )
        cells.append(cell)
        self.column_cells.setdefault(index, []).append(cell)

    def process_batch(self, stats: list[ColumnStats], rows: list[list[str]]) -> None:
        started = time.perf_counter()
        first_row = self.rows_analysed + 1
        for offset, row in enumerate(rows, start=first_row):
            self.rows_inspected += 1
            width = min(len(row), len(stats))
            for index, value in enumerate(row):
                if index >= len(stats):
                    continue
                column = stats[index]
                profile = profile_of(value)
                column.record(value, profile)
                self.cells_inspected += 1
                if "empty" in profile.tags:
                    self.empty_cells += 1
                    continue
                for detector in self._detectors_for(profile.tags):
                    self.detector_calls += 1
                    match = detector.match(value, profile, column.name)
                    if match is not None:
                        masked = mask_value(value)
                        column.add_hit(match, offset, masked)
                        # Cell evidence is recorded independently of whether the
                        # column as a whole is classified as personal data.
                        self._record_cell(offset, index, column, match, masked, detector, width)
        self.rows_analysed += len(rows)
        self.elapsed_rules += time.perf_counter() - started

    def row_records(self) -> list[RowRecord]:
        """Row-level verdicts: one record per row that contained at least one hit."""
        records = []
        for row, cells in sorted(self.row_cells.items()):
            context = analyse_row(cells)
            records.append(
                RowRecord(
                    row=row,
                    has_pii=context["has_pii"],
                    cells=cells,
                    pii_types=context["pii_types"],
                    review_types=context["review_types"],
                    column_names=sorted({cell.column for cell in cells}),
                    column_indexes=sorted({cell.column_index for cell in cells}),
                    basis=context["basis"],
                    confidence=max((cell.confidence for cell in cells), default=0.0),
                    needs_review=context["needs_review"],
                    risk_level=context["risk_level"],
                    cells_checked=self.row_widths.get(row, len(cells)),
                )
            )
        return records

    def cell_counts(self) -> dict:
        return {
            "rows_inspected": self.rows_inspected,
            "cells_inspected": self.cells_inspected,
            "empty_cells": self.empty_cells,
            "detector_calls": self.detector_calls,
            "rows_with_hits": self.rows_with_hits,
            "cell_hits": self.cell_hits,
            "pii_cells": self.pii_cells,
            "review_cells": self.review_cells,
            "ignored_cells": self.ignored_cells,
            "detail_limit": self.config.max_detail_rows,
            "detail_truncated_rows": self.detail_truncated_rows,
        }

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
                else:
                    # The column as a whole is not a privacy column, but the cells
                    # that matched keep their own verdict ("검토") instead of being
                    # dropped - a handful of genuine PII cells in an ordinary column
                    # must still reach the row-level report.
                    entry["decision"] = "부분 매칭(검토)"
                    entry["rule_confidence"] = round(min(confidence, 1.0) * min(1.0, ratio + 0.2), 3)
                    entry["column_dominance"] = round(ratio, 4)
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
