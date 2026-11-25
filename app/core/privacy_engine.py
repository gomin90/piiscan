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
from app.core import temp_store
from app.core.cell_verdict import (
    STATUS_EMPTY, STATUS_KEY, STATUS_NON, STATUS_PII, STATUS_REVIEW, STATUS_UNRATED,
    STATUS_RANK,
    rule_negative_verdict,
)
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
from app.detectors.embedded import (
    EmbeddedBirthDateDetector,
    EmbeddedCardDetector,
    EmbeddedCoordinateDetector,
    EmbeddedDeviceIdDetector,
    EmbeddedEmailDetector,
    EmbeddedMacDetector,
    EmbeddedPassportDetector,
    EmbeddedPhoneDetector,
    EmbeddedResidentIdDetector,
)
from app.detectors.base import Detector, profile_of
from app.detectors.bank_account import BankAccountDetector
from app.detectors.birth_date import BirthDateDetector
from app.detectors.credit_card import CreditCardDetector
from app.detectors.custom_identifier import CustomIdentifierDetector
from app.detectors.driver_license import DriverLicenseDetector
from app.detectors.email import EmailDetector
from app.detectors.free_text_name import FreeTextNameDetector
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
from app.detectors.context_policy import (
    AccountGuidDetector,
    AuthCredentialDetector,
    CellIdDetector,
    CustomerLinkageDetector,
    FaxNumberDetector,
    FaceBiometricDetector,
    GaidDetector,
    MaskedDataDetector,
    PaymentInfoDetector,
    PersonalAttributeDetector,
    RfidDetector,
    WirelessNetworkDetector,
)
from app.config.privacy_policy import get_policy
from app.detectors.base import ScanContext
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
        # Interior scanners for long free-text cells (JSON, logs, Base64 blobs).
        EmbeddedResidentIdDetector(),
        EmbeddedPhoneDetector(),
        EmbeddedCardDetector(),
        EmbeddedEmailDetector(),
        EmbeddedPassportDetector(),
        EmbeddedBirthDateDetector(),
        EmbeddedMacDetector(),
        EmbeddedCoordinateDetector(),
        EmbeddedDeviceIdDetector(),
        # Person names that appear inside sentences rather than as a bare value.
        FreeTextNameDetector(),
        # Types that cannot be decided by value form alone: the column meaning and
        # the value shape have to agree (see resources/rules/privacy_kr_2026.json).
        AuthCredentialDetector(),
        CustomerLinkageDetector(),
        CellIdDetector(),
        RfidDetector(),
        WirelessNetworkDetector(),
        FaxNumberDetector(),
        FaceBiometricDetector(),
        AccountGuidDetector(),
        PersonalAttributeDetector(),
        GaidDetector(),
        PaymentInfoDetector(),
        MaskedDataDetector(),
    ]
    policy = get_policy()
    for detector in detectors:
        definition = policy.get(detector.type_key)
        if definition is not None:
            detector.priority = definition.tier
            detector.sensitivity = definition.sensitivity
            detector.legal_category = definition.category
            if definition.column_aliases and not detector.name_hints:
                detector.name_hints = definition.column_aliases
    return detectors


class PrivacyEngine:
    def __init__(self, config: AnalysisConfig | None = None, store: DetailStore | None = None):
        self.config = config or AnalysisConfig()
        self.detectors = build_detectors(self.config)
        self.tag_detectors: dict[str, list[Detector]] = {}
        # Columns the user designated as record keys; their cells are never scanned.
        self.key_indexes: set[int] = set()
        for detector in self.detectors:
            for tag in detector.candidate_tags:
                self.tag_detectors.setdefault(tag, []).append(detector)
        # The first detector registered for a type owns its display metadata;
        # embedded variants share the type key but must not override it.
        self.by_type: dict[str, Detector] = {}
        for detector in self.detectors:
            self.by_type.setdefault(detector.type_key, detector)
        self.elapsed_rules = 0.0
        self.rows_analysed = 0
        # Rows already decided by a previous, cancelled run of the same file.
        self.row_offset = 0
        self.store = store or DetailStore(Path(tempfile.mkdtemp(prefix="piiscan-detail-")) / "detail.sqlite")
        if store is None:
            self.store.temp_dir = self.store.path.parent
            temp_store.register_store(self.store)
        # Raw scan counters - always exact, no detail cap applies to them.
        self.rows_inspected = 0
        self.cells_inspected = 0
        self.empty_cells = 0
        self.key_cells = 0
        self.extra_cells = 0
        self.extra_columns = 0
        self.header_count = 0
        self.detector_calls = 0
        self.cell_hits = 0
        # Cells that hold more than one occurrence of a privacy type, and the
        # extra occurrences found beyond the first one.
        self.multi_match_cells = 0
        self.occurrences_found = 0
        self.cell_occurrences = 0
        self.rows_with_hits = 0
        # Final verdict counters - filled by finalise_verdicts().
        self.pii_cells = 0
        self.review_cells = 0
        self.ignored_cells = 0
        # Cells the rules positively cleared, and cells they left undecided.
        self.cleared_cells = 0
        self.unrated_cells = 0
        # Bounded in-memory view of the verdicts for the GUI overview.
        self.row_verdicts: dict[int, RowRecord] = {}
        self.cached_cells = 0
        self._tag_cache: dict[frozenset[str], tuple[Detector, ...]] = {}
        #: Table / column context passed to detectors that need more than the
        #: single column name (name vs. product disambiguation).
        self.scan_context = ScanContext(table_name=self.config.table)
        self._context_width = 0
        self._occurrences_this_batch = 0
        self._column_context_cache: dict[str, tuple[str, str]] = {}
        self.policy = get_policy()
        self.key_names: tuple[str, ...] = ()
        # Rows are streamed in order, so the last row that produced a hit is all
        # that is needed to count distinct rows with hits (no unbounded set).
        self._last_hit_row = 0

    def _column_context(self, name: str) -> tuple[str, str]:
        """Semantic signature and context class of one column.

        The signature is the set of policy types the column name supports; the
        context class adds whether the record is keyed and which columns carry the
        KEY. Two cells may share an AI verdict only when both are identical.
        """
        cached = self._column_context_cache.get(name)
        if cached is not None:
            return cached
        policy = self.policy
        types = sorted(code for code in policy.types
                       if policy.column_affinity(code, name) >= 0.25)
        signature = ",".join(types)
        tier = min((policy.tier(code) for code in types), default=2)
        key_part = ",".join(sorted(self.key_names)) if self.key_names else "-"
        context = f"tier:{tier};key:{key_part}"
        result = (signature, context)
        self._column_context_cache[name] = result
        return result

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

    def process_batch(self, stats: list[ColumnStats], rows: list[list[str]],
                     line_spans: list[tuple[int, int]] | None = None) -> None:
        """Scan every cell of every row and record one verdict per cell.

        Cells of a user-selected KEY column are excluded from the privacy scan but
        still get a verdict (`KEY 제외`), so the Cell ledger stays complete and the
        KEY columns are visible in the report as excluded rather than missing.

        A row with more fields than the header keeps its extra fields: they become
        EXTRA_COL_n columns and are scanned like any other cell, so personal data
        that happens to sit past the declared header is never silently dropped.

        A verdict is written for *every* cell, not only for cells that matched a
        rule. A cell that matched is left unsettled until the column is complete
        (see `finalise_verdicts`); a cell that matched nothing is either positively
        cleared by the non-personal rules or, when the rules cannot decide, marked
        ``미평가`` so the model - or a reviewer - can still settle it.
        """
        started = time.perf_counter()
        first_row = self.row_offset + self.rows_analysed + 1
        self._occurrences_this_batch = 0
        self._column_context_cache: dict[str, tuple[str, str]] = {}
        self.policy = get_policy()
        self.key_names: tuple[str, ...] = ()
        self.key_names = tuple(stats[i].name for i in self.key_indexes if i < len(stats))
        if len(stats) != self._context_width:
            self._context_width = len(stats)
            self.scan_context = ScanContext(
                table_name=self.scan_context.table_name,
                column_names=tuple(column.name for column in stats),
                key_columns=tuple(sorted(stats[i].name for i in self.key_indexes if i < len(stats))),
            )
        spans = line_spans or [(0, 0)] * len(rows)
        # Column semantics are needed for the "different context, same value"
        # rule: two columns that mean the same thing may share an AI verdict,
        # columns that do not must not.
        # Column semantics (not cell values) - bounded by the number of columns.
        for offset, row, (line_start, line_end) in zip(range(first_row, first_row + len(rows)),
                                                      rows, spans):
            self.rows_inspected += 1
            width = len(row)
            extra = width - self.header_count
            if extra > 0:
                # Every field past the header is counted, per row, so the total
                # number of unexpectedly present cells is exact rather than the
                # number of columns that happened to be new.
                self.extra_cells += extra
                for index in range(len(stats), width):
                    stats.append(ColumnStats(name=f"EXTRA_COL_{index + 1}", index=index,
                                            max_hit_rows=self.config.max_hit_rows))
                # Distinct extra columns ever seen, not the widest row.
                self.extra_columns = max(self.extra_columns, len(stats) - self.header_count)
            key_values: list[str] = []
            for index, value in enumerate(row):
                column = stats[index]
                signature, context_class = self._column_context(column.name)
                if index in self.key_indexes:
                    # KEY columns identify the record; they are never judged as
                    # personal data, but the record still needs its fingerprint.
                    text = value.strip()
                    if text and text not in NULL_TOKENS:
                        key_values.append(value)
                    self.key_cells += 1
                    self.store.add_cell_verdict(offset, index, column.name, STATUS_KEY,
                                               rule_verdict="KEY 컬럼(분석 제외)",
                                               decision_method="KEY")
                    continue
                profile = profile_of(value)
                column.record(value, profile)
                self.cells_inspected += 1
                if "empty" in profile.tags:
                    self.empty_cells += 1
                    self.store.add_cell_verdict(offset, index, column.name, STATUS_EMPTY,
                                               rule_verdict="빈 셀", decision_method="규칙")
                    continue
                matched_types: list[str] = []
                fingerprint = ""
                spans: list[tuple[str, int, int]] = []
                for detector in self._detectors_for(profile.tags):
                    self.detector_calls += 1
                    matches = detector.find_matches(value, profile, column.name, self.scan_context)
                    if not matches:
                        continue
                    self._occurrences_this_batch += len(matches)
                    for match in matches:
                        match = detector.contextual_match(match, column.name, value)
                        if match is None:
                            continue
                        match.tier = detector.priority
                        match.legal_category = detector.legal_category
                        span = (match.type_key, match.match_start, match.match_end)
                        if span in spans:
                            # The same detector reporting the same span twice
                            # (for instance through both match() and find_matches)
                            # must not produce two evidences for one occurrence.
                            continue
                        spans.append(span)
                        # For interior matches the fragment is masked, never the
                        # whole (potentially huge) cell.
                        masked = mask_value(match.fragment or value)
                        if not fingerprint:
                            fingerprint = self.store.fingerprint(value)
                        column.add_hit(match, offset, masked)
                        if not matched_types:
                            # One hit Cell, however many detectors or occurrences
                            # produced its evidence.
                            self.cell_hits += 1
                        if offset > self._last_hit_row:
                            self._last_hit_row = offset
                            self.rows_with_hits += 1
                        matched_types.append(match.type_key)
                        self.store.add_raw_cell(offset, index, column.name, match, masked,
                                               detector.column_affinity(column.name), width,
                                               fingerprint=fingerprint)
                if matched_types:
                    if len(spans) > 1:
                        # One Cell holding more than one occurrence of personal data.
                        self.multi_match_cells += 1
                        self.occurrences_found += len(spans) - 1
                    # The rule verdict needs the finished column ratio, so the cell
                    # is parked as unsettled until the scan of the column is done.
                    self.store.add_cell_verdict(
                        offset, index, column.name, STATUS_UNRATED,
                        types=matched_types, rule_verdict="규칙 판정 대기",
                        masked_value=mask_value(value), decision_method="규칙",
                        needs_review=True, fingerprint=fingerprint,
                        tier=min((self.policy.tier(t) for t in matched_types), default=2),
                        semantic_signature=signature, context_class=context_class,
                        occurrences=len(spans), line_start=line_start, line_end=line_end,
                    )
                    continue
                cleared = rule_negative_verdict(value, column.name)
                if cleared:
                    label, evidence = cleared
                    self.cleared_cells += 1
                    self.store.add_cell_verdict(offset, index, column.name, STATUS_NON,
                                               rule_verdict=label, decision_method="규칙",
                                               evidence=evidence)
                else:
                    self.unrated_cells += 1
                    self.store.add_cell_verdict(offset, index, column.name, STATUS_UNRATED,
                                               rule_verdict="규칙 매칭 없음",
                                               masked_value=mask_value(value),
                                               decision_method="규칙", needs_review=True,
                                               evidence="규칙: 개인정보 패턴 없음 (AI/사람 판단 대기)",
                                               fingerprint=self.store.fingerprint(value),
                                               semantic_signature=signature,
                                               context_class=context_class,
                                               line_start=line_start, line_end=line_end)
            # Rows keep their own identity: the KEY fingerprint is stored next to
            # the row, never as part of the row key, so two rows with the same KEY
            # value (or with no KEY at all) remain two separate records.
            record_fingerprint = self.store.fingerprint("|".join(key_values)) if key_values else ""
            # The KEY values are kept only in masked form so the AI can see what
            # identifies the record without the report ever holding raw KEY data.
            key_mask = " | ".join(mask_value(value) for value in key_values[:6])
            self.store.close_row(record_fingerprint, f"r:{offset}", line_start, line_end,
                                 key_mask=key_mask)
        # One transaction per batch: far fewer fsync-bound commits than flushing
        # every buffer, while still surviving a crash mid-file.
        self.store.commit()
        self.rows_analysed += len(rows)
        self.cell_occurrences += self._occurrences_this_batch
        self.elapsed_rules += time.perf_counter() - started

    def finalise_verdicts(self, stats: list[ColumnStats]) -> list[RowRecord]:
        """Judge every recorded hit with the finished column ratio.

        Streams the detail store in row order, so RAM is bounded by the number of
        hits in a single row while the full evidence keeps its final verdict. The
        cell verdict table is updated in the same pass, which is what makes the
        `privacy_cells` report agree with `privacy_hits` cell by cell.
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
        # One merged rule verdict per cell: a cell can be matched by several
        # detectors, and folding them here keeps the UPDATE count at one per cell.
        merged: dict[tuple[int, int], list] = {}

        def flush_merged() -> None:
            if not merged:
                return
            updates = []
            for (row, index), (status, confirmed, candidates, conf, review, verdict,
                               evidence, start, end, span_text) in merged.items():
                updates.append((status,
                                ",".join(list(dict.fromkeys((confirmed or []) + (candidates or [])))),
                                round(conf, 3),
                                bool(review), row, index, verdict, evidence, start, end, span_text))
                if status == STATUS_PII:
                    self.pii_cells += 1
                elif status == STATUS_REVIEW:
                    self.review_cells += 1
            self.store.merge_cell_rule_verdicts(updates)
            merged.clear()

        for raw in self.store.iter_raw_cells():
            ratio = support.get((raw["column_index"], raw["type_key"]), 0.0)
            decision, confidence, needs_review = cell_decision(
                raw["confidence"], raw["confirmed"], ratio, raw["name_hint"]
            )
            if current_row is None or current_row != raw["row"]:
                if current_row is not None:
                    if cells:
                        self._flush_row(cells, width)
                    flush_merged()
                current_row = raw["row"]
                width = raw["cells_checked"]
                cells = []
            key = (raw["row"], raw["column_index"])
            entry = merged.get(key)
            span_start = raw["match_start"] if raw["match_start"] >= 0 else 0
            span_end = raw["match_end"] if raw["match_end"] > span_start else span_start + 1
            span_text = f"{raw['type_key']}@{span_start}-{span_end}"
            if decision == IGNORE_VERDICT:
                # A shape that only looks like personal data: the cell is cleared,
                # not left unjudged.
                self.ignored_cells += 1
                if entry is None:
                    merged[key] = [STATUS_NON, [], [], confidence, False,
                                   IGNORE_VERDICT,
                                   f"규칙: {raw['display_name']} 형식만 일치, 근거 부족",
                                   span_start, span_end, ""]
                continue
            if entry is None:
                merged[key] = [decision,
                               [raw["type_key"]] if decision == STATUS_PII else [],
                               [raw["type_key"]],
                               confidence, needs_review, raw["display_name"], raw["evidence"],
                               span_start, span_end, span_text]
            else:
                # Strongest verdict wins; a weaker match never downgrades a cell.
                if STATUS_RANK[decision] > STATUS_RANK[entry[0]]:
                    entry[0] = decision
                    entry[5] = raw["display_name"]
                    entry[6] = raw["evidence"]
                entry[3] = max(entry[3], confidence)
                entry[4] = entry[4] or needs_review
                entry[7] = min(entry[7], span_start)
                entry[8] = max(entry[8], span_end)
                if span_text not in entry[9].split(";"):
                    entry[9] = f"{entry[9]};{span_text}" if entry[9] else span_text
                if decision == STATUS_PII:
                    # Only a confirmed match may name a type on the Cell record; a
                    # downgraded match stays a candidate, listed in the hit ledger.
                    if raw["type_key"] and raw["type_key"] not in entry[1]:
                        entry[1].append(raw["type_key"])
                elif raw["type_key"] and raw["type_key"] not in entry[2]:
                    entry[2].append(raw["type_key"])
            pending.append((decision, round(confidence, 3), int(needs_review),
                            round(ratio, 4), raw["id"]))
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

        if current_row is not None and cells:
            self._flush_row(cells, width)
        flush_merged()
        if pending:
            self.store.set_verdicts(pending)
        self.store.flush()
        self.store.aggregate_row_verdicts()
        # Identical personal-data values are grouped in SQLite so the group report
        # can be streamed without keeping anything in memory.
        self.store.build_groups()
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
            "key_cells": self.key_cells,
            "key_columns": len(self.key_indexes),
            "logical_cells": self.cells_inspected + self.key_cells,  # empty cells are a subset of inspected
            "extra_cells": self.extra_cells,
            "extra_columns": self.extra_columns,
            "detector_calls": self.detector_calls,
            "rows_with_hits": self.rows_with_hits,
            "cell_hits": self.cell_hits,
            "multi_occurrence_cells": self.multi_match_cells,
            "extra_occurrences": self.occurrences_found,
            "pii_cells": self.pii_cells,
            "review_cells": self.review_cells,
            "ignored_cells": self.ignored_cells,
            "cleared_cells": self.cleared_cells,
            "unrated_cells": self.unrated_cells,
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
