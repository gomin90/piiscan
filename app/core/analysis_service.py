"""분석 파이프라인: CSV 검사 -> 규칙 전수 분석 -> 통계 -> AI -> 통합 -> 위험도."""
from __future__ import annotations

import hashlib
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.ai.gpu_detector import detect_hardware
from app.ai import create_ai_engine
from app.config.settings import AnalysisConfig
from app.core.code_group_analyzer import analyze as analyze_code_groups
from app.core.column_stats import ColumnStats
from app.utils.hyperloglog import BoundedSet
from app.core.csv_detector import detect_format, read_preview
from app.core.csv_loader import CsvLoader, FileSettings, file_size
from app.core.encryption_analyzer import analyze_protection
from app.core.cell_verdict import (
    STATUSES, STATUS_EMPTY, STATUS_NON, STATUS_PII, STATUS_REVIEW, STATUS_UNRATED,
)
from app.core.detail_store import DetailStore
from app.core import temp_store
from app.core.privacy_engine import PrivacyEngine
from app.core.result_aggregator import build_results
from app.utils.logger import Diagnostics, get_logger


@dataclass
class FileResult:
    path: str
    settings: dict
    profile: dict
    rows_parsed: int
    rows_error: int
    rows_empty: int
    rows_irregular: int
    rows_short: int
    rows_extra: int
    total_rows: int
    elapsed_seconds: float
    rows_unparsed: int = 0
    oversized_fields: int = 0
    field_char_limit: int = 0
    cancelled: bool = False
    parse_errors: list = field(default_factory=list)
    findings: list = field(default_factory=list)
    row_records: list = field(default_factory=list)
    cell_counts: dict = field(default_factory=dict)
    protection: list = field(default_factory=list)
    code_groups: list = field(default_factory=list)
    results: list = field(default_factory=list)
    ai_summary: dict = field(default_factory=dict)
    ai_audit: dict = field(default_factory=dict)
    detail_store: object = None
    analysis_id: str = ""
    preview: list = field(default_factory=list)
    timings: dict = field(default_factory=dict)
    resumed_from: int = 0

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "settings": self.settings,
            "format": self.profile,
            "rows_parsed": self.rows_parsed,
            "rows_error": self.rows_error,
            "rows_empty": self.rows_empty,
            "rows_irregular": self.rows_irregular,
            "rows_short": self.rows_short,
            "rows_extra": self.rows_extra,
            "rows_unparsed": self.rows_unparsed,
            "oversized_fields": self.oversized_fields,
            "field_char_limit": self.field_char_limit,
            "parse_errors": self.parse_errors,
            "total_rows": self.total_rows,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "cancelled": self.cancelled,
            "resumed_from_row": self.resumed_from,
            "findings": self.findings,
            "row_analysis": {
                "rows_inspected": self.cell_counts.get("rows_inspected", 0),
                "rows_with_pii_cells": self.cell_counts.get("detail_rows_stored", 0),
                "cell_hits": self.cell_counts.get("cell_hits", 0),
                "detail_rows_stored": self.cell_counts.get("detail_rows_stored", 0),
                "detail_cells_stored": self.cell_counts.get("detail_cells_stored", 0),
                "detail_cache_rows": self.cell_counts.get("detail_cache_rows", 0),
                "detail_cache_limit": self.cell_counts.get("detail_cache_limit", 0),
                "detail_cache_cells": self.cell_counts.get("detail_cache_cells", 0),
                "detail_cache_cell_limit": self.cell_counts.get("detail_cache_cell_limit", 0),
                "detail_store_path": self.cell_counts.get("detail_store_path", ""),
                "cell_records_stored": self.cell_counts.get("verdict_cells", 0),
                "cell_status_counts": self.cell_counts.get("verdict_status", {}),
                # The JSON only carries the in-memory cache; the complete detail
                # is in the SQLite store and in the CSV exports.
                "detail_rows_in_json": len(self.row_records),
                "details_complete": self.cell_counts.get("detail_rows_stored", 0) == len(self.row_records),
                "details_truncated": self.cell_counts.get("detail_rows_stored", 0) > len(self.row_records),
                "rows": [record.as_dict() for record in self.row_records],
            },
            "cell_analysis": {
                "logical_cells": self.cell_counts.get("logical_cells", 0),
                "key_excluded_cells": self.cell_counts.get("key_cells", 0),
                "verdict_complete": self.cell_counts.get("verdict_complete", True),
                "cells_inspected": self.cell_counts.get("cells_inspected", 0),
                "cells_empty": self.cell_counts.get("empty_cells", 0),
                "cells_rule_cleared": self.cell_counts.get("cleared_cells", 0),
                "cells_unrated": self.cell_counts.get("unrated_cells", 0),
                "cell_hits": self.cell_counts.get("cell_hits", 0),
                "cell_pii": self.cell_counts.get("pii_cells", 0),
                "cell_review": self.cell_counts.get("review_cells", 0),
                "cell_ignored": self.cell_counts.get("ignored_cells", 0),
                "cell_records_stored": self.cell_counts.get("verdict_cells", 0),
                "cell_status_counts": self.cell_counts.get("verdict_status", {}),
                "rows_with_pii": self.cell_counts.get("rows_with_hits", 0),
            },
            "protection": [g.as_dict() for g in self.protection],
            "code_groups": [g.as_dict() for g in self.code_groups],
            "results": [r.as_dict() for r in self.results],
            "ai": self.ai_summary,
            "timings": self.timings,
        }


# Rule engine verdicts at or above this confidence are considered settled: the AI
# is not asked about them unless the user explicitly asks for every column.
SETTLED_RULE_CONFIDENCE = 0.85
# How many cells of a row the AI is shown at most.
CONFIG_ROW_CONTEXT_LIMIT = 12
# Cells the AI is asked about when the rules could not decide.
AI_PENDING_STATUSES = (STATUS_UNRATED, STATUS_REVIEW)
# The Jev-Style readout has three options, so a bare plurality (p ~ 0.34) is not
# evidence. Below this probability the model's "personal" answer only warrants a
# human review, never a confirmed hit.
AI_CONFIRM_CONFIDENCE = 0.60


def merge_cell_verdict(rule_status: str, rule_confidence: float,
                       ai_decision: str | None, ai_confidence: float) -> tuple[str, float, str, bool]:
    """Combine the rule verdict and the model verdict for one cell.

    Rules and AI are complementary: the model can promote an undecided cell, but
    it can never erase a rule-confirmed personal-data verdict by itself - that
    case is flagged for human review instead.
    """
    if ai_decision is None:
        return rule_status, rule_confidence, "규칙", True
    if ai_decision == "PERSONAL_DATA":
        if rule_status == STATUS_PII:
            return STATUS_PII, max(rule_confidence, ai_confidence), "규칙+AI", False
        if ai_confidence < AI_CONFIRM_CONFIDENCE:
            # The model leans personal but is not sure: never confirm on a
            # plurality vote, and never lose the possibility either.
            return STATUS_REVIEW, ai_confidence, "AI", True
        if rule_status in AI_PENDING_STATUSES:
            return STATUS_PII, ai_confidence, "AI", rule_status == STATUS_REVIEW
        # A rule that positively cleared the cell and the model that says it is
        # personal data disagree: keep the cell under review.
        return STATUS_REVIEW, min(rule_confidence, ai_confidence), "규칙+AI", True
    if ai_decision == "NON_PERSONAL_DATA":
        if rule_status == STATUS_PII:
            # The model may not overwrite a confirmed hit on its own.
            return STATUS_PII, rule_confidence, "규칙", True
        if rule_status == STATUS_NON:
            return STATUS_NON, max(rule_confidence, ai_confidence), "규칙+AI", False
        if rule_status == STATUS_UNRATED:
            return STATUS_NON, ai_confidence, "AI", False
        return STATUS_REVIEW, rule_confidence, "규칙", True
    return STATUS_REVIEW, min(rule_confidence, 0.5), "규칙+AI", True



def ai_column_candidates(findings: list[dict], config: AnalysisConfig) -> list[dict]:
    """Columns that genuinely need a semantic judgement, ranked by uncertainty.

    The rule engine already scanned every cell, so a column whose pattern is
    confirmed, dominant and unopposed does not need the model. What the AI is good
    at is ambiguous naming ("AMOUNT" holding card-shaped numbers), competing types
    and weak evidence. Ranking by priority rather than file order means the most
    doubtful columns - wherever they sit in the file - are the ones reviewed.
    Cell recall is untouched: every cell is still rule-checked.
    """
    if config.ai_column_reviews == "none":
        return []
    if config.ai_column_reviews == "all":
        # Explicit "judge every column" mode: identical to the pre-v0.5 behaviour,
        # bounded only by max_ai_column_reviews.
        return [{"finding": finding, "reason": "전부 AI 판단", "priority": 0.0} for finding in findings]

    scored: list[dict] = []
    for finding in findings:
        types = finding["types"]
        weak = finding["weak_types"]
        if not types and not weak:
            continue  # no detector evidence at all: nothing for the model to judge
        all_types = types + weak
        distinct_types = {t["type_key"] for t in all_types}
        best = max((t["rule_confidence"] for t in types), default=0.0)
        # Settled means one strong, unopposed type: no weak evidence, no competing
        # type, and a rule confidence the model could not improve on.
        settled = (len(types) == 1 and not weak and len(distinct_types) == 1
                   and best >= SETTLED_RULE_CONFIDENCE)
        if settled:
            continue
        uncertainty = 1.0 - best
        conflict = max(0, len(distinct_types) - 1)
        weak_evidence = len(weak)
        sensitivity = max((t["sensitivity"] for t in all_types), default=0.0)
        hits = sum(t["hits"] for t in all_types)
        hint = finding.get("name_affinity", 0.0)
        priority = (uncertainty + 0.35 * conflict + 0.25 * weak_evidence
                    + 0.2 * sensitivity + 0.1 * min(hint, 1.0) + 0.001 * min(hits, 500))
        if conflict:
            reason = "경합하는 개인정보 유형"
        elif weak_evidence:
            reason = "약한/부분 탐지"
        else:
            reason = "규칙 판정 불확실"
        scored.append({"finding": finding, "reason": reason,
                       "priority": round(priority, 4), "types": sorted(distinct_types)})

    scored.sort(key=lambda item: (-item["priority"], item["finding"]["index"]))
    return scored


def select_column_candidates(candidates: list[dict], limit: int) -> list[dict]:
    """Pick `limit` columns that represent the whole file.

    The highest-priority columns are taken first, then the remaining slots are
    spread evenly over the rest of the list so the columns at the right hand end
    of a wide file are not silently dropped.
    """
    if limit <= 0 or not candidates:
        return []
    if len(candidates) <= limit:
        return candidates
    head = min(limit, max(1, limit * 3 // 4))
    chosen = candidates[:head]
    rest = candidates[head:]
    remaining = limit - head
    if remaining and rest:
        if remaining == 1:
            chosen.append(rest[-1])
        else:
            # The right-most candidate is always taken: in a wide file the last
            # columns would otherwise never be sampled no matter how uncertain
            # their verdict is.
            rightmost = max(rest, key=lambda item: item["finding"]["index"])
            chosen.append(rightmost)
            pool = [item for item in rest if item is not rightmost]
            slots = remaining - 1
            for index in range(slots):
                chosen.append(pool[index * max(1, len(pool) - 1) // max(1, slots - 1)] if slots > 1 else pool[0])
    return chosen


def row_context(raw: dict, store, settings: FileSettings) -> dict:
    """Masked, risk-ordered Row evidence for the AI - never a raw value.

    Cells are ordered by risk so the model sees the settled evidence, the
    ambiguous evidence and the surrounding context, not simply the first few
    columns of the file.
    """
    cells = list(store.cells_between_rows(raw["row"], raw["row"]))
    cells.sort(key=lambda cell: (-cell["confidence"], cell["column_index"]))
    ordered = []
    for cell in cells[: CONFIG_ROW_CONTEXT_LIMIT]:
        ordered.append({
            "column": cell["column"], "column_index": cell["column_index"],
            "type": cell["type_key"], "display_name": cell["display_name"],
            "value": cell["masked_value"], "decision": cell["decision"],
            "confidence": cell["confidence"], "evidence": cell["evidence"],
            "column_support": cell["column_support"], "name_hint": cell["name_hint"],
        })
    return {
        "row": raw["row"],
        "schema": settings.schema,
        "table": settings.table,
        "status": raw["status"],
        "basis": raw["basis"],
        "key_mask": raw.get("key_mask", ""),
        "line_start": raw.get("line_start") or raw["row"],
        "line_end": raw.get("line_end") or raw["row"],
        "risk_level": raw["risk_level"],
        "confirmed_types": raw["confirmed_pii"],
        "possible_types": raw["possible_pii"],
        "cells_checked": raw["cells_checked"],
        "cell_count": raw["cell_count"],
        "cells": ordered,
        "cell_evidence": ordered,
        "distinct_types": sorted({cell["type"] for cell in ordered}),
    }


def review_cells(store: DetailStore, llm, columns: list[dict], config: AnalysisConfig,
                 audit: dict, diagnostics: Diagnostics, report_ai: callable = None) -> None:
    """Ask the local model about the cells the rules could not settle.

    Questions are batched per column (Jev-Style shares one decision state per
    request), so a batch of 25 cells costs one scorer call, not 25. Only the
    masked value and the rule evidence are sent; raw cell text never leaves the
    rule engine. Cells the model is not asked about keep their 미평가 status.
    """
    cell_audit = {"policy": config.ai_cell_reviews, "limit": config.max_ai_cell_reviews,
                  "candidates": 0, "attempted": 0, "success": 0, "failed": 0,
                  "skipped": 0, "budget_skipped": 0, "memory_skipped": 0,
                  "unavailable_skipped": 0, "reused": 0, "context_reused": 0,
                  "batches": 0, "decisions": 0, "seconds": 0.0,
                  "inference_seconds": 0.0, "model_load_seconds": 0.0}
    if config.ai_cell_reviews == "none" or config.max_ai_cell_reviews <= 0:
        cell_audit["skipped"] = store.verdict_count() - store.verdict_count(STATUS_EMPTY)
        cell_audit["budget_skipped"] = cell_audit["skipped"]
        audit["cell"] = cell_audit
        diagnostics.record("ai_cell_review", **cell_audit)
        return
    if llm is None or not hasattr(llm, "judge_cells"):
        cell_audit["skipped"] = store.verdict_count() - store.verdict_count(STATUS_EMPTY)
        cell_audit["failed"] = cell_audit["skipped"]
        cell_audit["unavailable_skipped"] = cell_audit["skipped"]
        audit["cell"] = cell_audit
        diagnostics.record("ai_cell_review", **cell_audit)
        return

    statuses = STATUSES if config.ai_cell_reviews == "all" else AI_PENDING_STATUSES
    statuses = tuple(status for status in statuses if status != STATUS_EMPTY)
    cell_audit["candidates"] = store.candidate_count(statuses)
    cell_audit["model_load_seconds"] = round(getattr(llm, "load_seconds", 0.0), 3)
    column_by_index = {column["index"]: column for column in columns}
    started = time.perf_counter()
    # A batch is keyed by the reuse key so identical Cells in the same batch share
    # one scorer question instead of one question each.
    batch: dict[tuple, list[dict]] = {}
    batch_column: int | None = None
    # A verdict is reusable only when the value AND the column's semantic signature
    # AND the context class match. `ai_context_reuse` off forces a fresh inference
    # for every cell. See spec section 9 (Reused = Cell AI Reuse Count).
    reuse: dict[tuple, tuple[str, float, str, bool, str]] = {}
    context_reuse = getattr(config, "ai_context_reuse", True)

    def reuse_key(cell: dict) -> tuple:
        return (
            cell.get("masked_value", ""),
            cell.get("semantic_signature") or cell["column"],
            cell.get("context_class", ""),
            cell["rule_verdict"],
            tuple(cell.get("privacy_types", "").split(",")),
        )

    def apply_verdict(cell: dict, decision: str, status: str, confidence: float,
                      method: str, needs_review: bool) -> None:
        store.set_cell_ai_verdicts([(decision, status, confidence, method, needs_review,
                                    cell["row"], cell["column_index"])])

    def flush() -> None:
        nonlocal batch, batch_column
        if not batch:
            return
        first_group = next(iter(batch.values()))
        column = column_by_index.get(batch_column,
                                     {"column": first_group[0]["column"], "index": batch_column})
        representatives = [group[0] for group in batch.values()]
        duplicates = sum(len(group) - 1 for group in batch.values())
        if duplicates:
            cell_audit["reused"] += duplicates
            cell_audit["context_reused"] += duplicates
        cell_audit["attempted"] += len(representatives)
        cell_audit["batches"] += 1
        call_started = time.perf_counter()
        try:
            results = llm.judge_cells(column, representatives)
        except Exception as exc:  # a scorer failure must not lose the rule verdict
            logger.warning("Cell AI 판단 실패: %s", exc)
            cell_audit["failed"] += len(representatives)
            batch = {}
            return
        cell_audit["inference_seconds"] += time.perf_counter() - call_started
        updates = []
        for (key, group), result in zip(batch.items(), results):
            cell_audit["decisions"] += len(group)
            if result.get("decision") is None:
                cell_audit["failed"] += len(group)
                continue
            cell_audit["success"] += len(group)
            for cell in group:
                status, confidence, method, needs_review = merge_cell_verdict(
                    cell["cell_status"], cell["confidence"], result["decision"], result["confidence"]
                )
                updates.append((result["decision"], status, confidence, method, needs_review,
                                cell["row"], cell["column_index"]))
                reuse[key] = (result["decision"], status, confidence, method, needs_review)
        if updates:
            store.set_cell_ai_verdicts(updates)
        batch = {}
        if report_ai is not None:
            report_ai(cell_audit["attempted"] + cell_audit["reused"])

    for cell in store.iter_candidates(statuses, config.max_ai_cell_reviews):
        key = reuse_key(cell)
        if context_reuse:
            cached = reuse.get(key)
            if cached is not None:
                decision, status, confidence, method, needs_review = cached
                apply_verdict(cell, cell["ai_verdict"] or decision, status, confidence,
                              method, needs_review)
                cell_audit["reused"] += 1
                cell_audit["context_reused"] += 1
                continue
        if batch_column is not None and cell["column_index"] != batch_column:
            flush()
        batch_column = cell["column_index"]
        batch.setdefault(key, []).append(cell)
        if len(batch) >= config.ai_cell_batch_size:
            flush()
        if llm.memory_usage().get("rss_mb", 0) > 3500:
            logger.warning("RAM 한계 감지 -> Cell AI 추론 중단, 규칙 기반 결과 보존")
            batch = {}
            cell_audit["memory_skipped"] = max(
                0, cell_audit["candidates"] - cell_audit["attempted"] - cell_audit["reused"])
            break
    flush()

    not_judged = max(0, cell_audit["candidates"] - cell_audit["attempted"] - cell_audit["reused"])
    cell_audit["budget_skipped"] = max(0, not_judged - cell_audit["memory_skipped"])
    cell_audit["skipped"] = not_judged
    cell_audit["seconds"] = round(time.perf_counter() - started, 3)
    cell_audit["inference_seconds"] = round(cell_audit["inference_seconds"], 3)
    store.write_row_verdicts()  # row summary must reflect the merged cell verdicts
    audit["cell"] = cell_audit
    diagnostics.record("ai_cell_review", **cell_audit)


def detect_analysis_mode(total_rows: int, requested: str, sample_rows: int = 1000) -> str:
    if requested == "full":
        return "전수"
    if requested == "sampling":
        # 실데이터 전량 대신 앞 N행만 분석한다 (report에 분석 행 수 함께 기록).
        return f"샘플링({min(sample_rows, total_rows or sample_rows)}행)"
    # 최근 100개 추출 CSV는 샘플링, 그 외 파일은 전수 분석으로 표시한다.
    return "sampling(최근 100개)" if total_rows <= 100 else "전수"


def analyse_file(
    path: str | Path,
    config: AnalysisConfig | None = None,
    llm=None,
    progress: callable = None,
    diagnostics: Diagnostics | None = None,
    overrides: dict | None = None,
    should_stop: callable = None,
) -> FileResult:
    config = config or AnalysisConfig()
    logger = get_logger()
    diagnostics = diagnostics or Diagnostics()
    started = time.perf_counter()
    path = str(path)

    # 1단계: 파일 형식 검사
    profile = detect_format(path)
    overrides = dict(overrides or {})
    overrides.setdefault("table", config.table)
    overrides.setdefault("schema", config.schema)
    overrides.setdefault("db_type", config.db_type)
    settings = FileSettings.from_profile(profile, **overrides)
    diagnostics.record("format_detected", encoding=settings.encoding, delimiter=repr(settings.delimiter),
                       header=settings.has_header, columns=profile.column_count)

    loader = CsvLoader(settings, max_field_chars=config.max_field_chars)
    loader.prepare()
    mode = detect_analysis_mode(loader.total_rows or 0, config.mode, config.sample_rows)
    # "sampling" genuinely truncates the scan; "full"/"auto" stream every row.
    row_limit = config.sample_rows if config.mode == "sampling" else None
    diagnostics.record("analysis_mode", mode=mode, row_limit=row_limit)

    # 2단계: 스트리밍 규칙 기반 전수 분석 (batch 1,000행)
    # Each file gets its own database unless the user named one explicitly; in
    # that shared case every record still carries an analysis_id, so Row 7 of
    # file A is never confused with Row 7 of file B.
    temp_dir: Path | None = None
    if config.detail_store_path:
        detail_path = Path(config.detail_store_path)
    else:
        # Temporary stores are tracked by app.core.temp_store so they are always
        # removed, even on cancellation, an exception, or a SIGTERM.
        temp_store.sweep_orphans()
        temp_dir = Path(tempfile.mkdtemp(prefix="piiscan-detail-"))
        detail_path = temp_dir / "detail.sqlite"
    analysis_id = f"{Path(path).stem}-{hashlib.sha1(str(Path(path).resolve()).encode()).hexdigest()[:10]}"
    store = DetailStore(detail_path, analysis_id, kind=config.analysis_kind,
                      resume=bool(config.resume))
    store.temp_dir = temp_dir
    temp_store.register_store(store)
    # `--resume` continues a cancelled run of the same file. The checkpoint is
    # read before registration so it is not overwritten by it.
    resume_from = store.resume_row() if config.resume else 0
    store.register_run(path, settings.schema, settings.table, settings.db_type,
                       resume=bool(resume_from))
    if resume_from:
        diagnostics.record("resume_checkpoint", resumed_from_row=resume_from)
    engine = PrivacyEngine(config, store)
    # KEY columns are chosen by the user, never guessed: an unmatched name is
    # reported as ignored instead of silently narrowing the scan.
    wanted = {name.strip().lower() for name in config.key_columns if name.strip()}
    engine.key_indexes = {index for index, name in enumerate(loader.column_names)
                          if name.strip().lower() in wanted}
    unmatched_keys = wanted - {name.strip().lower() for name in loader.column_names}
    if unmatched_keys:
        diagnostics.record("key_columns_ignored", columns=sorted(unmatched_keys),
                           available=loader.column_names[:20])
    diagnostics.record("key_columns", selected=[name for index, name in enumerate(loader.column_names)
                                                if index in engine.key_indexes],
                       key_cells_expected=(loader.total_rows or 0) * len(engine.key_indexes))
    # The declared header width, not the widest row seen so far, is what makes a
    # field "extra".
    engine.header_count = loader.header_length or len(loader.column_names)
    engine.row_offset = resume_from
    column_count = max(1, len(loader.column_names))
    # Distinct-value memory scales with the number of columns, so the per-column
    # exact set is sized from the file-wide budget (max_distinct_tracked) while
    # HyperLogLog keeps the true cardinality estimate.
    distinct_per_column = max(20, config.max_distinct_tracked // column_count)
    hll_precision = 12 if column_count <= 500 else 10
    stats = [ColumnStats(name=name, index=i, max_hit_rows=config.max_hit_rows,
                         distinct=BoundedSet(limit=distinct_per_column, p=hll_precision))
             for i, name in enumerate(loader.column_names)]
    diagnostics.record("memory_budget", columns=column_count,
                       distinct_per_column=distinct_per_column, hll_precision=hll_precision)
    if resume_from:
        diagnostics.record("resume_stats_seeded", **store.seed_column_stats(stats))
    batch: list[list[str]] = []
    batch_lines: list[tuple[int, int]] = []
    cancelled = False
    code_sample: list[list[str]] = []
    last_progress_rows = 0
    last_progress_cells = 0
    batch_seconds = 0.0

    def report_progress() -> None:
        """Advance progress on rows *and* cell throughput.

        A 10,000-column file with 100 rows is one tenth of a million cells of
        work but never reaches the row threshold, so cell counts are the signal
        that the scan is actually making progress.
        """
        nonlocal last_progress_rows, last_progress_cells
        if not progress:
            return
        if (loader.parsed_rows - last_progress_rows >= 5000
                or engine.cells_inspected - last_progress_cells >= 200_000
                or loader.parsed_rows >= loader.progress_total()):
            last_progress_rows = loader.parsed_rows
            last_progress_cells = engine.cells_inspected
            progress(loader.parsed_rows, loader.progress_total(), engine.cells_inspected)
    for index, row, line_start, line_end in loader.iter_rows():
        # `loader` numbers rows from 0 while the ledger numbers them from 1, so a
        # checkpoint of 40 means rows 1..40 are already decided.
        if resume_from and index < resume_from:
            continue
        if row_limit is not None and engine.rows_analysed >= row_limit:
            diagnostics.record("sampling_cutoff", rows_analysed=engine.rows_analysed,
                               total_rows=loader.total_rows)
            break
        if should_stop is not None and should_stop():
            diagnostics.record("analysis_cancelled", rows_analysed=engine.rows_analysed,
                               cells_inspected=engine.cells_inspected,
                               total_rows=loader.total_rows)
            cancelled = True
            break
        batch.append(row)
        batch_lines.append((line_start, line_end))
        if len(code_sample) < 500:
            code_sample.append(row)
        if len(batch) >= config.batch_size:
            t0 = time.perf_counter()
            engine.process_batch(stats, batch, batch_lines)
            batch_seconds += time.perf_counter() - t0
            batch = []
            batch_lines = []
            store.register_columns([column.name for column in stats])
            store.mark_last_row(resume_from + engine.rows_analysed)
            report_progress()

    if batch:
        t0 = time.perf_counter()
        engine.process_batch(stats, batch, batch_lines)
        batch_seconds += time.perf_counter() - t0
        store.register_columns([column.name for column in stats])
        store.mark_last_row(resume_from + engine.rows_analysed)
        report_progress()

    findings = engine.finalise(stats)
    row_records = engine.finalise_verdicts(stats)
    cell_counts = engine.cell_counts()
    completeness = store.verify_completeness()
    diagnostics.record("cell_completeness", **completeness)
    if not completeness["complete"]:
        logger.error("Cell ledger incomplete: %d logical cells, %d verdicts",
                     completeness["logical_cells"], completeness["verdict_cells"])
    diagnostics.record("rows_parsed", parsed=loader.parsed_rows, errors=loader.error_rows,
                       unparsed=loader.unparsed_rows, empty=loader.empty_rows,
                       irregular=loader.irregular_rows, short=loader.short_rows,
                       extra=loader.extra_rows, oversized_fields=loader.oversized_fields,
                       field_char_limit=loader.max_field_chars)
    diagnostics.record("cell_scan", **cell_counts)

    # 3단계: 보호/코드 그룹 분석
    protection_groups = analyze_protection(stats, findings)
    code_groups = analyze_code_groups(loader.column_names, code_sample, stats,
                                      privacy_columns={f["column"] for f in findings})

    # 4단계: CSV parsing resources 정리 (stats만 유지) -> 5/6단계 AI
    llm = llm or create_ai_engine(config, detect_hardware())
    ai_summary = llm.summary()
    ai_judgements: dict[str, dict] = {}
    ai_audit = {"selected": 0, "skipped": 0, "attempts": 0, "success": 0, "failed": 0,
                "policy": config.ai_column_reviews, "limit": config.max_ai_column_reviews}
    if llm.load():
        candidates = ai_column_candidates(findings, config)
        selected = select_column_candidates(candidates, config.max_ai_column_reviews)
        ai_audit["candidates"] = len(candidates)
        ai_audit["selected"] = len(selected)
        ai_audit["skipped"] = len(findings) - len(selected)
        for item in selected:
            finding = item["finding"]
            ai_audit["attempts"] += 1
            judgement = llm.judge_column({**finding, "table": settings.table, "select_reason": item["reason"]})
            if judgement:
                ai_audit["success"] += 1
                ai_judgements[finding["column"]] = judgement
            else:
                ai_audit["failed"] += 1
            rss_mb = llm.memory_usage().get("rss_mb") or 0
            if rss_mb > 3500:
                logger.warning("RAM 한계 감지 -> AI 추론 중단, 규칙 기반 결과 보존")
                ai_audit["skipped"] += len(selected) - ai_audit["attempts"]
                llm.close()
                break
        else:
            if len(candidates) > len(selected):
                diagnostics.record("ai_column_cap", limit=config.max_ai_column_reviews,
                                   candidates=len(candidates), selected=len(selected))
        # Cell-level semantic judgement: the rules inspected every cell, the model
        # is asked only about the cells the rules could not settle.
        def ai_progress(ai_cells: int) -> None:
            if progress is not None:
                progress(loader.parsed_rows, loader.progress_total(), engine.cells_inspected, ai_cells)

        review_cells(store, llm, findings, config, ai_audit, diagnostics, report_ai=ai_progress)
        # Row 문맥 판정은 규칙이 결론 내리지 못한 Row에만, 제한된 횟수만큼 호출된다.
        # 후보는 detail cache가 아니라 전체 store에서 뽑는다: cap 때문에 AI이 파일
        # 앞부분 Row만 보는 편향을 막는다.
        review_candidates = store.review_rows(config.max_ai_row_reviews)
        total_candidates = store.review_row_count()
        row_audit = {"candidates": total_candidates, "attempts": 0, "success": 0,
                     "failed": 0, "skipped": max(0, total_candidates - len(review_candidates))}
        for raw in review_candidates:
            row_audit["attempts"] += 1
            verdict = llm.judge_row(row_context(raw, store, settings))
            if verdict:
                row_audit["success"] += 1
                store.update_ai_review(raw["row"], verdict)
                cached = engine.row_verdicts.get(raw["row"])
                if cached is not None:
                    cached.ai_review = verdict
            else:
                row_audit["failed"] += 1
            if llm.memory_usage().get("rss_mb", 0) > 3500:
                logger.warning("RAM 한계 감지 -> AI Row 추론 중단, 규칙 기반 결과 보존")
                row_audit["skipped"] += len(review_candidates) - row_audit["attempts"]
                break
        ai_audit["row"] = row_audit
        diagnostics.record("ai_row_review", **row_audit)
    for group in code_groups:
        if group.decision == "pending" and llm.llm is not None:
            ai = llm.judge_code_group(group.as_dict())
            if ai:
                group.decision = "ai"
                group.confidence = max(group.confidence, ai.get("confidence", 0.0))
                group.note = ai.get("meaning", group.note)
    llm.close()
    ai_summary = llm.summary()

    results = build_results(
        file=Path(path).name,
        db_type=settings.db_type,
        schema=settings.schema,
        table=settings.table,
        mode=mode,
        rows_analysed=loader.parsed_rows,
        findings=findings,
        protection_groups=protection_groups,
        ai_judgements=ai_judgements,
    )

    elapsed = time.perf_counter() - started
    diagnostics.record("analysis_complete", elapsed=round(elapsed, 3), rules=round(engine.elapsed_rules, 3))
    return FileResult(
        path=path,
        settings={"encoding": settings.encoding, "delimiter": settings.delimiter,
                  "header": settings.has_header, "table": settings.table, "schema": settings.schema,
                  "db_type": settings.db_type, "mode": mode, "size_bytes": file_size(path),
                  "analysed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
        profile=profile.as_dict(),
        rows_parsed=loader.parsed_rows,
        rows_error=loader.error_rows,
        rows_empty=loader.empty_rows,
        rows_irregular=loader.irregular_rows,
        rows_short=loader.short_rows,
        rows_extra=loader.extra_rows,
        rows_unparsed=loader.unparsed_rows,
        oversized_fields=loader.oversized_fields,
        field_char_limit=loader.field_limit,
        parse_errors=list(loader.row_errors),
        total_rows=loader.total_rows or loader.parsed_rows,
        elapsed_seconds=elapsed,
        findings=findings,
        row_records=row_records,
        cell_counts=cell_counts,
        protection=protection_groups,
        code_groups=code_groups,
        results=results,
        ai_summary=ai_summary,
        ai_audit=ai_audit,
        detail_store=store,
        analysis_id=analysis_id,
        preview=read_preview(path, settings.encoding, settings.delimiter, 20),
        timings={"rules": round(engine.elapsed_rules, 3), "batch": round(batch_seconds, 3)},
        cancelled=cancelled,
        resumed_from=resume_from,
    )
