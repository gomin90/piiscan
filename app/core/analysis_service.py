"""분석 파이프라인: CSV 검사 -> 규칙 전수 분석 -> 통계 -> AI -> 통합 -> 위험도."""
from __future__ import annotations

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
from app.core.detail_store import DetailStore
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
    findings: list = field(default_factory=list)
    row_records: list = field(default_factory=list)
    cell_counts: dict = field(default_factory=dict)
    protection: list = field(default_factory=list)
    code_groups: list = field(default_factory=list)
    results: list = field(default_factory=list)
    ai_summary: dict = field(default_factory=dict)
    ai_audit: dict = field(default_factory=dict)
    detail_store: object = None
    preview: list = field(default_factory=list)
    timings: dict = field(default_factory=dict)

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
            "total_rows": self.total_rows,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "findings": self.findings,
            "row_analysis": {
                "rows_inspected": self.cell_counts.get("rows_inspected", 0),
                "rows_with_pii_cells": self.cell_counts.get("detail_rows_stored", 0),
                "cell_hits": self.cell_counts.get("cell_hits", 0),
                "detail_rows_stored": self.cell_counts.get("detail_rows_stored", 0),
                "detail_cells_stored": self.cell_counts.get("detail_cells_stored", 0),
                "detail_cache_rows": self.cell_counts.get("detail_cache_rows", 0),
                "detail_cache_limit": self.cell_counts.get("detail_cache_limit", 0),
                "detail_store": self.cell_counts.get("detail_store", ""),
                "rows": [record.as_dict() for record in self.row_records],
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


def ai_column_candidates(findings: list[dict], config: AnalysisConfig) -> list[dict]:
    """Columns that genuinely need a semantic judgement.

    The rule engine already scanned every cell, so a column whose pattern is
    confirmed and dominant does not need the model. What the AI is good at is
    ambiguous naming ("AMOUNT" holding card-shaped numbers), competing types and
    partial matches. Selecting those keeps cell recall untouched - every cell is
    still rule-checked - while avoiding thousands of model calls on a 10,000
    column file.
    """
    if config.ai_column_reviews == "none":
        return []
    if config.ai_column_reviews == "all":
        # Explicit "judge every column" mode: identical to the pre-v0.5 behaviour,
        # bounded only by max_ai_column_reviews.
        return [{"finding": finding, "reason": "전부 AI 판단"} for finding in findings]
    candidates = []
    for finding in findings:
        types = finding["types"]
        weak = finding["weak_types"]
        if not types and not weak:
            continue  # no detector evidence at all: nothing for the model to judge
        best = max((t["rule_confidence"] for t in types), default=0.0)
        settled = bool(types) and len(types) == 1 and best >= SETTLED_RULE_CONFIDENCE
        if not settled:
            candidates.append({"finding": finding, "reason": "규칙 판정 불확실"})
    return candidates


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
        "risk_level": raw["risk_level"],
        "confirmed_types": raw["confirmed_pii"],
        "possible_types": raw["possible_pii"],
        "cells_checked": raw["cells_checked"],
        "cell_count": raw["cell_count"],
        "cells": ordered,
        "cell_evidence": ordered,
        "distinct_types": sorted({cell["type"] for cell in ordered}),
    }


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

    loader = CsvLoader(settings)
    loader.prepare()
    mode = detect_analysis_mode(loader.total_rows or 0, config.mode, config.sample_rows)
    # "sampling" genuinely truncates the scan; "full"/"auto" stream every row.
    row_limit = config.sample_rows if config.mode == "sampling" else None
    diagnostics.record("analysis_mode", mode=mode, row_limit=row_limit)

    # 2단계: 스트리밍 규칙 기반 전수 분석 (batch 1,000행)
    detail_path = Path(config.detail_store_path) if config.detail_store_path \
        else Path(tempfile.mkdtemp(prefix="piiscan-detail-")) / "detail.sqlite"
    store = DetailStore(detail_path)
    engine = PrivacyEngine(config, store)
    engine.header_count = len(loader.column_names)
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
    batch: list[list[str]] = []
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
    for index, row in loader.iter_rows():
        if row_limit is not None and engine.rows_analysed >= row_limit:
            diagnostics.record("sampling_cutoff", rows_analysed=engine.rows_analysed,
                               total_rows=loader.total_rows)
            break
        batch.append(row)
        if len(code_sample) < 500:
            code_sample.append(row)
        if len(batch) >= config.batch_size:
            t0 = time.perf_counter()
            engine.process_batch(stats, batch)
            batch_seconds += time.perf_counter() - t0
            batch = []
            report_progress()

    if batch:
        t0 = time.perf_counter()
        engine.process_batch(stats, batch)
        batch_seconds += time.perf_counter() - t0
        report_progress()

    findings = engine.finalise(stats)
    row_records = engine.finalise_verdicts(stats)
    cell_counts = engine.cell_counts()
    diagnostics.record("rows_parsed", parsed=loader.parsed_rows, errors=loader.error_rows,
                       empty=loader.empty_rows, irregular=loader.irregular_rows,
                       short=loader.short_rows, extra=loader.extra_rows)
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
        ai_audit["selected"] = len(candidates)
        ai_audit["skipped"] = len(findings) - len(candidates)
        for item in candidates[: config.max_ai_column_reviews]:
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
                ai_audit["skipped"] += len(candidates) - ai_audit["attempts"]
                llm.close()
                break
        else:
            ai_audit["skipped"] += len(candidates) - min(len(candidates), config.max_ai_column_reviews)
            if len(candidates) > config.max_ai_column_reviews:
                diagnostics.record("ai_column_cap", limit=config.max_ai_column_reviews,
                                   candidates=len(candidates))
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
        preview=read_preview(path, settings.encoding, settings.delimiter, 20),
        timings={"rules": round(engine.elapsed_rules, 3), "batch": round(batch_seconds, 3)},
    )
