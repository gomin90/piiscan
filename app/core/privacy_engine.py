"""Rule based privacy detection engine.

Combines regex patterns, checksums, data statistics and column-name meaning.
Each detector is independent, so a new privacy type only needs a new detector
class registered in `build_detectors`.

Two phases:

1. **Scan** - every non-empty cell of every row is inspected by the rule engine and
   every match is streamed, as raw evidence, to the detail store (SQLite).
2. **Verdict** - once the whole column is known, each match is judged with its own
   evidence plus the finished column ratio. Verdicts therefore do not depend on the
   order the rows were read in.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import Callable

from app.config.settings import AnalysisConfig
from app.core.column_stats import ColumnStats
from app.core.detail_store import DetailStore
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

# Number of matched rows kept per (column, type) for the column-level detail output.
DETAIL_ROW_LIMIT = 20

# Columns whose names say "internal record key" (NO/ID/CD/CODE/SEQ) are not
# personal data just because the values happen to look like an ID format.
IDENTIFIER_NAME_TOKENS = ("no", "id", "cd", "code", "seq", "num", "key", "ref")
GENERIC_CONFIDENCE = 0.85      # below this the pattern alone is not conclusive

# Dominance test for the *column-level* verdict: a column is declared a privacy
# column only when the matching values dominate it and that content is confirmed.
DOMINANT_RATIO = 0.50
HINTED_RATIO = 0.30
WEAK_RATIO = 0.20


def hit_rows_for(column: ColumnStats, type_key: str, limit: int = DETAIL_ROW_LIMIT) -> list[dict]:
    """Row numbers of the values that matched, with masked values only."""
    return column.hit_rows.get(type_key, [])[:limit]


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
    return detectors


class PrivacyEngine:
    def __init__(self, config: AnalysisConfig | None = None, store: DetailStore | None = None):
        self.config = config or AnalysisConfig()
        self.detectors = build_detectors(self.config)
        self.tag_detectors: dict[str, list[Detector]] = {}
        for detector in self.detectors:
            for tag in detector.candidate_tags:
                self.tag_detectors.setdefault(tag, []).append(detector)
        self.by_type = {detector.type_key: detector for detector in self.detectors}
        self.elapsed_rules = 0.0
        self.rows_analysed = 0
        self.store = store or DetailStore(Path(tempfile.mkdtemp(prefix="piiscan-detail-")) / "detail.sqlite")
        # Raw scan counters - always exact, no detail cap applies to them.
        self.rows_inspected = 0
        self.cells_inspected = 0
        self.empty_cells = 0
        self.extra_cells = 0
        self.extra_columns = 0
        self.header_count = 0
        self.detector_calls = 0
        self.cell_hits = 0
        self.rows_with_hits = 0
        # Final verdict counters - filled by finalise_verdicts().
        self.pii_cells = 0
        self.review_cells = 0
        self.ignored_cells = 0
        # Bounded in-memory view of the verdicts for the GUI overview.
        self.row_verdicts: dict[int, RowRecord] = {}
        self.cached_cells = 0
        self._tag_cache: dict[frozenset[str], tuple[Detector, ...]] = {}
        # Rows are streamed in order, so the last row that produced a hit is all
        # that is needed to count distinct rows with hits (no unbounded set).
        self._last_hit_row = 0

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

    def process_batch(self, stats: list[ColumnStats], rows: list[list[str]]) -> None:
        """Scan every cell of every row.

        A row with more fields than the header keeps its extra fields: they become
        EXTRA_COL_n columns and are scanned like any other cell, so personal data
        that happens to sit past the declared header is never silently dropped.
        """
        started = time.perf_counter()
        first_row = self.rows_analysed + 1
        for offset, row in enumerate(rows, start=first_row):
            self.rows_inspected += 1
            width = len(row)
            if width > len(stats):
                self.extra_cells += width - len(stats)
                for index in range(len(stats), width):
                    stats.append(ColumnStats(name=f"EXTRA_COL_{index + 1}", index=index,
                                            max_hit_rows=self.config.max_hit_rows))
                self.extra_columns = max(self.extra_columns, width - self.header_count)
            for index, value in enumerate(row):
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
                        self.cell_hits += 1
                        if offset > self._last_hit_row:
                            self._last_hit_row = offset
                            self.rows_with_hits += 1
                        self.store.add_raw_cell(offset, index, column.name, match, masked,
                                               detector.column_affinity(column.name), width)
        self.rows_analysed += len(rows)
        self.elapsed_rules += time.perf_counter() - started

    def finalise_verdicts(self, stats: list[ColumnStats]) -> list[RowRecord]:
        """Judge every recorded hit with the finished column ratio.

        Streams the detail store in row order, so RAM is bounded by the number of
        hits in a single row while the full evidence keeps its final verdict.
        """
        support: dict[tuple[int, str], float] = {}
        for column in stats:
            for hit in column.hits.values():
                support[(column.index, hit.type_key)] = (
                    hit.count / column.non_empty if column.non_empty else 0.0
                )

        current_row: int | None = None
        width = 0
        cells: list[CellRecord] = []
        pending: list[tuple] = []
        for raw in self.store.iter_raw_cells():
            ratio = support.get((raw["column_index"], raw["type_key"]), 0.0)
            decision, confidence, needs_review = cell_decision(
                raw["confidence"], raw["confirmed"], ratio, raw["name_hint"]
            )
            if decision == IGNORE_VERDICT:
                self.ignored_cells += 1
                continue
            if decision == "개인정보":
                self.pii_cells += 1
            else:
                self.review_cells += 1
            if current_row is None or current_row != raw["row"]:
                if current_row is not None:
                    self._flush_row(cells, width)
                current_row = raw["row"]
                width = raw["cells_checked"]
                cells = []
            pending.append((decision, round(confidence, 3), int(needs_review), round(ratio, 4), raw["id"]))
            if len(pending) >= 1000:
                self.store.set_verdicts(pending)
                pending.clear()
            cells.append(CellRecord(
                row=raw["row"], column_index=raw["column_index"], column=raw["column"],
                type_key=raw["type_key"], display_name=raw["display_name"],
                confidence=round(confidence, 3), masked_value=raw["masked_value"],
                evidence=raw["evidence"], confirmed=raw["confirmed"], masked=raw["masked"],
                sensitivity=raw["sensitivity"], priority=raw["priority"],
                decision=decision, needs_review=needs_review, column_support=round(ratio, 4),
            ))

        if current_row is not None:
            self._flush_row(cells, width)
        if pending:
            self.store.set_verdicts(pending)
        self.store.flush()
        return list(self.row_verdicts.values())

    def _flush_row(self, cells: list[CellRecord], width: int) -> None:
        context = analyse_row(cells)
        record = RowRecord(
            row=cells[0].row,
            has_pii=context["has_pii"],
            cells=cells,
            pii_types=context["pii_types"],
            review_types=context["review_types"],
            confirmed_pii=context["confirmed_pii"],
            possible_pii=context["possible_pii"],
            status=context["status"],
            column_names=sorted({cell.column for cell in cells}),
            column_indexes=sorted({cell.column_index for cell in cells}),
            basis=context["basis"],
            confidence=max((cell.confidence for cell in cells), default=0.0),
            needs_review=context["needs_review"],
            risk_level=context["risk_level"],
            cells_checked=width,
            cell_count=len(cells),
        )
        self.store.write_row_detail(record)
        if len(self.row_verdicts) < self.config.max_detail_rows:
            # Keep the cell list only while the RAM budget allows it; the full
            # cell detail always lives in SQLite.
            if self.cached_cells + len(cells) <= self.config.max_detail_cells:
                self.cached_cells += len(cells)
            else:
                record.cells = []
            self.row_verdicts[record.row] = record

    def row_page(self, offset: int = 0, limit: int | None = None) -> list[RowRecord]:
        """Read one page of row verdicts (with its cell detail) from the store."""
        rows = list(self.store.iter_rows(offset=offset, limit=limit))
        if not rows:
            return []
        cell_map: dict[int, list[CellRecord]] = {}
        for raw in self.store.cells_between_rows(rows[0]["row"], rows[-1]["row"]):
            cell_map.setdefault(raw["row"], []).append(CellRecord.from_row(raw))
        records = []
        for raw in rows:
            record = RowRecord.from_row(raw)
            record.cells = cell_map.get(raw["row"], [])
            record.cell_count = len(record.cells)
            records.append(record)
        return records

    def cell_counts(self) -> dict:
        return {
            "rows_inspected": self.rows_inspected,
            "cells_inspected": self.cells_inspected,
            "empty_cells": self.empty_cells,
            "extra_cells": self.extra_cells,
            "detector_calls": self.detector_calls,
            "rows_with_hits": self.rows_with_hits,
            "cell_hits": self.cell_hits,
            "pii_cells": self.pii_cells,
            "review_cells": self.review_cells,
            "ignored_cells": self.ignored_cells,
            "detail_cache_rows": len(self.row_verdicts),
            "detail_cache_limit": self.config.max_detail_rows,
            "detail_cache_cells": self.cached_cells,
            "detail_cache_cell_limit": self.config.max_detail_cells,
            **self.store.stats(),
        }

    def finalise(self, stats: list[ColumnStats]) -> list[dict]:
        findings = []
        for column in stats:
            affinity = max((d.column_affinity(column.name) for d in self.detectors), default=0.0)
            types: list[dict] = []
            weak: list[dict] = []
            for hit in column.hits.values():
                ratio = hit.count / column.non_empty if column.non_empty else 0.0
                confidence = hit.avg_confidence
                detector = self.by_type.get(hit.type_key)
                type_affinity = detector.column_affinity(column.name) if detector else 0.0
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
