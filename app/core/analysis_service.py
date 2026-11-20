"""분석 파이프라인: CSV 검사 -> 규칙 전수 분석 -> 통계 -> AI -> 통합 -> 위험도."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from app.ai.gpu_detector import detect_hardware
from app.ai import create_ai_engine
from app.config.settings import AnalysisConfig
from app.core.code_group_analyzer import analyze as analyze_code_groups
from app.core.column_stats import ColumnStats
from app.core.csv_detector import detect_format, read_preview
from app.core.csv_loader import CsvLoader, FileSettings, file_size
from app.core.encryption_analyzer import analyze_protection
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
    total_rows: int
    elapsed_seconds: float
    findings: list = field(default_factory=list)
    protection: list = field(default_factory=list)
    code_groups: list = field(default_factory=list)
    results: list = field(default_factory=list)
    ai_summary: dict = field(default_factory=dict)
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
            "total_rows": self.total_rows,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "findings": self.findings,
            "protection": [g.as_dict() for g in self.protection],
            "code_groups": [g.as_dict() for g in self.code_groups],
            "results": [r.as_dict() for r in self.results],
            "ai": self.ai_summary,
            "timings": self.timings,
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
    engine = PrivacyEngine(config)
    stats = [ColumnStats(name=name, index=i, max_hit_rows=config.max_hit_rows)
             for i, name in enumerate(loader.column_names)]
    batch: list[list[str]] = []
    code_sample: list[list[str]] = []
    last_progress = 0.0
    batch_seconds = 0.0
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
            if progress and loader.parsed_rows - last_progress >= 10000:
                last_progress = loader.parsed_rows
                progress(loader.parsed_rows, loader.progress_total())

    if batch:
        t0 = time.perf_counter()
        engine.process_batch(stats, batch)
        batch_seconds += time.perf_counter() - t0

    findings = engine.finalise(stats)
    diagnostics.record("rows_parsed", parsed=loader.parsed_rows, errors=loader.error_rows,
                       empty=loader.empty_rows, irregular=loader.irregular_rows)

    # 3단계: 보호/코드 그룹 분석
    protection_groups = analyze_protection(stats, findings)
    code_groups = analyze_code_groups(loader.column_names, code_sample, stats,
                                      privacy_columns={f["column"] for f in findings})

    # 4단계: CSV parsing resources 정리 (stats만 유지) -> 5/6단계 AI
    llm = llm or create_ai_engine(config, detect_hardware())
    ai_summary = llm.summary()
    ai_judgements: dict[str, dict] = {}
    if llm.load():
        for finding in findings:
            judgement = llm.judge_column({**finding, "table": settings.table})
            if judgement:
                ai_judgements[finding["column"]] = judgement
            rss_mb = llm.memory_usage().get("rss_mb") or 0
            if rss_mb > 3500:
                logger.warning("RAM 한계 감지 -> AI 추론 중단, 규칙 기반 결과 보존")
                llm.close()
                break
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
                  "db_type": settings.db_type, "mode": mode, "size_bytes": file_size(path)},
        profile=profile.as_dict(),
        rows_parsed=loader.parsed_rows,
        rows_error=loader.error_rows,
        rows_empty=loader.empty_rows,
        rows_irregular=loader.irregular_rows,
        total_rows=loader.total_rows or loader.parsed_rows,
        elapsed_seconds=elapsed,
        findings=findings,
        protection=protection_groups,
        code_groups=code_groups,
        results=results,
        ai_summary=ai_summary,
        preview=read_preview(path, settings.encoding, settings.delimiter, 20),
        timings={"rules": round(engine.elapsed_rules, 3), "batch": round(batch_seconds, 3)},
    )
