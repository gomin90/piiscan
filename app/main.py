"""Privacy Scanner 실행 진입점 (CLI + GUI).

CLI:
  python app/main.py --file data.csv --output-dir results
  python app/main.py --files a.csv b.csv --mode full --no-ai
  python app/main.py --gui
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# Allow `python app/main.py` as well as `python -m app.main`.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import __version__
from app.ai import create_ai_engine
from app.ai.gpu_detector import detect_hardware
from app.config.settings import AnalysisConfig, DEFAULT_MAX_COLS, DEFAULT_MAX_ROWS
from app.core.analysis_service import analyse_file
from app.core.progress import ProgressTracker, make_cli_tracker
from app.export.csv_exporter import write_csv
from app.export.hit_exporter import (write_cell_records, write_group_records,
                        write_hit_rows, write_record_records, write_row_records)
from app.export.txt_exporter import write_txt
from app.utils.logger import Diagnostics, get_logger


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="폐쇄망 독립형 개인정보 탐지 및 분석 시스템")
    parser.add_argument("--file", action="append", default=[], help="분석할 CSV 파일 (반복 가능)")
    parser.add_argument("--files", nargs="*", default=[], help="분석할 CSV 파일 목록")
    parser.add_argument("--folder", help="폴더 단위 분석 (CSV만 선택)")
    parser.add_argument("--output-dir", default="results", help="결과 CSV/TXT 저장 위치")
    parser.add_argument("--mode", choices=["auto", "sampling", "full"], default="auto")
    parser.add_argument("--analysis", choices=["table", "metadata"], default="table",
                        help="분석 기능: table=실제 테이블 데이터(Function A), "
                             "metadata=DB 메타정보(Function B)")
    parser.add_argument("--key", action="append", default=[],
                        help="Function A: 레코드 식별 KEY 컬럼 (반복 가능, 자동 추론하지 않음)")
    parser.add_argument("--field-map", action="append", default=[],
                        help="Function B: ROLE=HEADER 필드 매핑 (예: --field-map table_name=TBL_NAME)")
    parser.add_argument("--resume", action="store_true",
                        help="--detail-store에 이미 analysed된 Row 이후부터 계속 분석")
    parser.add_argument("--table", default="", help="테이블명")
    parser.add_argument("--schema", default="", help="스키마명")
    parser.add_argument("--db", default="", help="원본 DB 종류")
    parser.add_argument("--encoding", default=None, help="인코딩 강제 (예: cp949)")
    parser.add_argument("--delimiter", default=None, help="구분자 강제 (, ; \t |)")
    parser.add_argument("--header", choices=["yes", "no"], default=None, help="헤더 존재 여부")
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--no-ai", action="store_true", help="AI 보조 판단 비활성화")
    parser.add_argument("--ai-engine", choices=["auto", "jev", "llm"], default="auto",
                        help="AI 엔진: auto(로컬 Jev-Style 우선), jev, llm")
    parser.add_argument("--jev-model", default="auto", help="Jev-Style 모델 key (0.8b-v3, 2b-v4)")
    parser.add_argument("--cpu-only", action="store_true", help="Metal/GPU 없이 CPU로 AI 추론")
    parser.add_argument("--gpu-layers", dest="gpu_layers", type=int, default=None,
                        help="GPU로 offload할 layer 수 (0 = CPU). 기본: GPU backend 설치 시 999")
    parser.add_argument("--detail-store", default="", help="Row/Cell 상세 SQLite 저장 경로 (기본: 임시 파일)")
    parser.add_argument("--max-detail-rows", type=int, default=5000,
                        help="RAM에 유지할 Row 판정 수 (전체 상세는 SQLite에 저장)")
    parser.add_argument("--max-detail-cells", type=int, default=50000,
                        help="RAM Cell 캐시 한도 (전체 Cell 상세는 SQLite)")
    parser.add_argument("--max-ai-row-reviews", type=int, default=100, help="AI Row 문맥 판단 최대")
    parser.add_argument("--max-field-chars", type=int, default=10_000_000,
                        help="CSV 필드 최대 길이 (초과 필드는 parse error로 집계)")
    parser.add_argument("--max-rows", type=int, default=DEFAULT_MAX_ROWS,
                        help="분석할 최대 데이터 Row 수 (0 = 무제한). 초과 Row는 미검사로 보고")
    parser.add_argument("--max-cols", type=int, default=DEFAULT_MAX_COLS,
                        help="분석할 최대 Col 수 (0 = 무제한). 초과 필드는 미검사로 보고")
    parser.add_argument("--progress", choices=["auto", "plain", "none"], default="auto",
                        help="진행 표시: auto(TTY 한 줄 갱신) / plain(주기 로그) / none")
    parser.add_argument("--progress-interval", type=float, default=0.25,
                        help="진행 표시 갱신 주기 (초)")
    parser.add_argument("--gui-cell-page-size", type=int, default=200,
                        help="GUI에서 한 화면에 렌더링하는 Cell 상세 수")
    parser.add_argument("--max-ai-column-reviews", type=int, default=200, help="AI 컬럼 판단 최대 횟수")
    parser.add_argument("--ai-column-reviews", choices=["selective", "all", "none"], default="selective",
                        help="AI 컬럼 판단 범위: 규칙으로 확정된 컬럼 skipping(selective) / 전부(all) / 없음(none)")
    parser.add_argument("--max-ai-cell-reviews", type=int, default=2000,
                        help="AI Cell 판단 최대 Cell 수 (batch 추론)")
    parser.add_argument("--ai-cell-reviews", choices=["selective", "all", "none"], default="selective",
                        help="AI Cell 판단 범위: 규칙 미확정 Cell(selective) / 전부(all) / 없음(none)")
    parser.add_argument("--ai-cell-batch-size", type=int, default=25,
                        help="Jev-Style shared-state batch에 보낼 Cell 수")
    parser.add_argument("--no-cell-report", action="store_true",
                        help="privacy_cells CSV(전체 Cell 판정) Export 생략")
    parser.add_argument("--debug", action="store_true", help="진단 로그 출력")
    parser.add_argument("--json", action="store_true", help="JSON 요약 stdout")
    parser.add_argument("--report-json", help="실행/진단 결과를 JSON 파일로 저장 (원문 개인정보 없음)")
    parser.add_argument("--gui", action="store_true", help="PySide6 GUI 실행")
    parser.add_argument("--version", action="version", version=f"Privacy Scanner {__version__}")
    return parser


def collect_files(args) -> list[str]:
    files = list(args.file) + list(args.files)
    if args.folder:
        folder = Path(args.folder)
        files.extend(str(p) for p in sorted(folder.glob("*.csv")))
        files.extend(str(p) for p in sorted(folder.glob("*.txt")))
    return files


def run_metadata_cli(args, config, files, llm, hardware, diagnostics, logger,
                     tracker: ProgressTracker | None = None) -> int:
    """Function B: DB metadata analysis, kept separate from the table scan."""
    from app.metadata_analysis.field_mapping import FieldMapping
    from app.metadata_analysis.metadata_service import analyse_metadata_file
    from app.metadata_analysis.reports import (write_metadata_columns, write_metadata_review,
                                              write_metadata_summary, write_metadata_tables)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    summaries: list[dict] = []
    stores = []
    for path in files:
        overrides = {}
        if args.encoding:
            overrides["encoding"] = args.encoding
        if args.delimiter:
            overrides["delimiter"] = args.delimiter if args.delimiter != "\\t" else "\t"
        profile_overrides = overrides or None
        mapping = None
        if args.field_map:
            from app.metadata_analysis.field_mapping import FieldMapping as _FM
            detected = {}
            # Overrides are applied after auto-detection inside the service; here we
            # only collect them so the service can validate role/header pairs.
            mapping = {"overrides": {pair.split("=", 1)[0].strip().lower(): pair.split("=", 1)[1].strip()
                                     for pair in args.field_map if "=" in pair}}
        if tracker is not None:
            tracker.start_file(files.index(path) + 1, Path(path).name, None)
        result = analyse_metadata_file(path, config, llm, mapping=mapping,
                                       diagnostics=diagnostics, overrides=overrides or None,
                                       tracker=tracker,
                                       progress=(lambda done, total: tracker.row_progress(done, total))
                                       if tracker is not None else None)
        store = result["store"]
        stores.append(store)
        if tracker is not None:
            tracker.export_progress(0, 4)
            tracker.stage("export")
        columns_path = write_metadata_columns(store, output_dir / f"metadata_columns_{timestamp}.csv")
        if tracker is not None:
            tracker.export_progress(1, 4)
        tables_path = write_metadata_tables(store, output_dir / f"metadata_tables_{timestamp}.csv")
        if tracker is not None:
            tracker.export_progress(2, 4)
        review_path = write_metadata_review(store, output_dir / f"metadata_review_{timestamp}.csv")
        if tracker is not None:
            tracker.export_progress(3, 4)
        summary_path = output_dir / f"metadata_report_{timestamp}.txt"
        write_metadata_summary(store, summary_path, result["summary"])
        if tracker is not None:
            tracker.export_progress(4, 4)
        summary = {**result["summary"], "file": path,
                   "field_mapping": result["result"]["mapping"].describe(),
                   "ai": result["result"]["ai"],
                   "seconds": round(result["result"]["rules_seconds"] + result["result"]["ai_seconds"], 2),
                   "reports": {"columns": columns_path, "tables": tables_path,
                               "review": review_path, "summary": str(summary_path)}}
        summaries.append(summary)
        logger.info(
            "%s -> 테이블 %d / 컬럼 %d (개인정보 관련 %d, 검토 %d, 관련 낮음 %d) %.2fs",
            Path(path).name, summary["tables"], summary["columns"], summary["relevant_columns"],
            summary["review_columns"], summary["low_columns"], summary["seconds"],
        )
        if summary["ai"].get("skipped_reason"):
            logger.info("AI 검토: %s", summary["ai"]["skipped_reason"])
    if tracker is not None:
        tracker.stage("cleanup")
    for store in stores:
        status = store.cleanup()
        logger.info("메타정보 저장소: %s (removed=%s)", status["detail_store_path"],
                    status["detail_store_removed"])
    if tracker is not None:
        tracker.finish()
        renderer = getattr(tracker, "renderer", None)
        if renderer is not None:
            renderer.finish()
        logger.info("완료: 파일 %d건 / 분석 metadata Row %d건 / Export %d건"
                    " / 미검사 Row %d건 / 미검사 Col %d건",
                    len(summaries),
                    sum(s.get("metadata_rows", 0) for s in summaries),
                    4 * len(summaries),
                    sum(s.get("rows_excluded", 0) for s in summaries),
                    sum(s.get("columns_excluded", 0) for s in summaries))
    report = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "analysis_kind": "metadata",
        "hardware": hardware.as_dict(),
        "ai": llm.summary(),
        "files": summaries,
    }
    if args.debug:
        diagnostics.dump(output_dir / f"diagnostics_{timestamp}.json")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.report_json:
        path = Path(args.report_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("진단 보고: %s", path)
    return 0


def run_cli(args) -> int:
    config = AnalysisConfig.load()
    config.batch_size = args.batch_size
    config.mode = args.mode
    config.table = args.table
    config.schema = args.schema
    config.db_type = args.db
    config.use_ai = not args.no_ai
    config.ai_engine = args.ai_engine
    config.jev_model = args.jev_model
    config.gpu_layers = args.gpu_layers
    config.debug = args.debug
    config.detail_store_path = args.detail_store
    config.max_detail_rows = args.max_detail_rows
    config.max_detail_cells = args.max_detail_cells
    config.max_ai_row_reviews = args.max_ai_row_reviews
    config.max_ai_column_reviews = args.max_ai_column_reviews
    config.ai_column_reviews = args.ai_column_reviews
    config.max_ai_cell_reviews = args.max_ai_cell_reviews
    config.ai_cell_reviews = args.ai_cell_reviews
    config.ai_cell_batch_size = max(1, args.ai_cell_batch_size)
    config.max_field_chars = args.max_field_chars
    config.max_rows = args.max_rows
    config.max_cols = args.max_cols
    config.gui_cell_page_size = args.gui_cell_page_size
    config.key_columns = list(args.key)
    config.analysis_kind = args.analysis
    config.resume = args.resume

    logger = get_logger()
    diagnostics = Diagnostics(enabled=args.debug)
    hardware = detect_hardware()
    if args.cpu_only:
        hardware.backend = "cpu"
        config.gpu_layers = 0
    logger.info("하드웨어: %s", hardware.as_dict())

    files = collect_files(args)
    if not files:
        logger.error("분석할 CSV 파일이 없습니다. --file 또는 --folder 를 지정하세요.")
        return 2

    # Progress goes to stdout unless --json reserves it, in which case the JSON
    # owns stdout and progress is written to stderr. Logging always uses stderr,
    # so the two never interleave on one line.
    tracker = make_cli_tracker(stream=sys.stderr if args.json else sys.stdout,
                               interval=args.progress_interval, mode=args.progress)

    llm = create_ai_engine(config, hardware)
    if config.analysis_kind == "metadata":
        return run_metadata_cli(args, config, files, llm, hardware, diagnostics, logger, tracker)
    results = []
    written = 0
    try:
        # A temporary detail store must not survive an exception: the finally
        # block below always releases it (important on Windows, where a leaked
        # WAL file keeps the directory dirty).
        for index, path in enumerate(files, start=1):
            overrides = {}
            if args.encoding:
                overrides["encoding"] = args.encoding
            if args.delimiter:
                overrides["delimiter"] = args.delimiter if args.delimiter != "\\t" else "\t"
            if args.header:
                overrides["has_header"] = args.header == "yes"
            result = analyse_file(path, config, llm, progress=tracker, diagnostics=diagnostics,
                                  overrides=overrides, file_index=index, file_total=len(files))
            results.append(result)
            counts = result.cell_counts
            if not result.scope.get("complete", True):
                logger.warning(
                    "분석 범위 제한: %s", result.scope.get("text", "-"))
            logger.info(
                "%s -> Row %d / Cell %d 전수 검사, 개인정보 Row %d건, 컬럼-유형 %d건, "
                "위험도 높음 %d건 (%.2fs)",
                Path(path).name,
                counts.get("rows_inspected", 0),
                counts.get("cells_inspected", 0),
                # Row count comes from the store, not the memory cache, so a file with
                # more hits than max_detail_rows is not under-reported.
                counts.get("detail_rows_stored", len(result.row_records)),
                len(result.results),
                sum(1 for r in result.results if r.risk_level in ("매우 높음", "높음")),
                result.elapsed_seconds,
            )
            logger.info(
                "Row 단위: Cell hits %d (빈 Cell %d, 규칙 호출 %d), 상세 저장 Row %d / Cell %d (%s), AI 판단 %d회",
                counts.get("cell_hits", 0),
                counts.get("empty_cells", 0),
                counts.get("detector_calls", 0),
                counts.get("detail_rows_stored", 0),
                counts.get("detail_cells_stored", 0),
                counts.get("detail_store_path", ""),
                result.ai_summary.get("decisions", 0),
            )
            audit = result.ai_audit
            if audit:
                logger.info(
                    "AI 감사: 컬럼 판단 %d 시도(성공 %d, 실패 %d, 생략 %d) / Row 판단 %d 시도(성공 %d, 실패 %d, 생략 %d)",
                    audit.get("attempts", 0), audit.get("success", 0), audit.get("failed", 0), audit.get("skipped", 0),
                    audit.get("row", {}).get("attempts", 0), audit.get("row", {}).get("success", 0),
                    audit.get("row", {}).get("failed", 0), audit.get("row", {}).get("skipped", 0),
                )

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = Path(args.output_dir)
        flat_results = [row for result in results for row in result.results]
        total_exports = 7 if not args.no_cell_report else 6
        tracker.export_progress(0, total_exports)
        tracker.stage("export")

        def exported(path: str | Path) -> None:
            nonlocal written
            written += 1
            tracker.export_progress(written, total_exports)

        csv_path = write_csv(flat_results, output_dir / f"privacy_results_{timestamp}.csv")
        exported(csv_path)
        txt_path = write_txt(results, output_dir / f"privacy_report_{timestamp}.txt")
        exported(txt_path)
        hits_path = write_hit_rows(results, output_dir / f"privacy_hits_{timestamp}.csv")
        exported(hits_path)
        rows_path = write_row_records(results, output_dir / f"privacy_rows_{timestamp}.csv")
        exported(rows_path)
        cell_report = ""
        if not args.no_cell_report:
            cell_report = write_cell_records(results, output_dir / f"privacy_cells_{timestamp}.csv")
            exported(cell_report)
        records_path = write_record_records(results, output_dir / f"privacy_records_{timestamp}.csv")
        exported(records_path)
        groups_path = write_group_records(results, output_dir / f"privacy_groups_{timestamp}.csv")
        exported(groups_path)
        logger.info("결과: %s / %s / %s / %s%s (Export %d Cell hit / %d Row / %d Cell verdict)",
                    csv_path, txt_path, hits_path, rows_path,
                    " / " + cell_report if cell_report else "",
                    write_hit_rows.rows_written, write_row_records.rows_written,
                    write_cell_records.rows_written)
        logger.info("Record %d건 / PII group %d건 -> %s / %s",
                    write_record_records.rows_written, write_group_records.rows_written,
                    records_path, groups_path)
    finally:
        tracker.stage("cleanup")
        cleanup: list[dict] = []
        handled: set[str] = set()
        for result in results:
            store = getattr(result, "detail_store", None)
            if store is None or store.path in handled:
                continue
            handled.add(store.path)
            status = store.cleanup()
            cleanup.append(status)
            if status["temporary"]:
                logger.info("임시 상세 저장소 정리: %s (removed=%s)",
                            status["detail_store_path"], status["detail_store_removed"])
            else:
                logger.info("상세 저장소 유지 (--detail-store): %s", status["detail_store_path"])
        processed_rows = sum(result.rows_parsed for result in results)
        processed_cells = sum(result.cell_counts.get("cells_inspected", 0) for result in results)
        excluded_rows = sum(result.scope.get("rows_excluded", 0) for result in results)
        excluded_cols = sum(result.scope.get("columns_excluded", 0) for result in results)
        tracker.finish()
        renderer = getattr(tracker, "renderer", None)
        if renderer is not None:
            renderer.finish()
        logger.info("완료: 파일 %d건 / 분석 Row %d건 / Cell %d건 / Export %d건"
                    " / 미검사 Row %d건 / 미검사 Col %d건",
                    len(results), processed_rows, processed_cells, written,
                    excluded_rows, excluded_cols)
    for result in results:
        result.cell_counts["detail_store_cleanup"] = next(
            (c["detail_store_removed"] for c in cleanup
             if c["detail_store_path"] == result.cell_counts.get("detail_store_path")), False)

    report = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "hardware": hardware.as_dict(),
        "ai": llm.summary(),
        "files": [r.as_dict() for r in results],
    }

    if args.debug:
        diagnostics.dump(output_dir / f"diagnostics_{timestamp}.json")

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))

    if args.report_json:
        path = Path(args.report_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("진단 보고: %s", path)
    return 0


def main(argv: list[str] | None = None) -> int:
    from app.core import temp_store

    # Re claim space left by runs that were killed before they could clean up.
    swept = temp_store.sweep_orphans()
    if swept["removed"]:
        print(f"임시 상세 저장소 {swept['removed']}개 정리 "
              f"({swept['bytes'] / 1024 / 1024:.1f} MB 회수)", file=sys.stderr)
    args = build_parser().parse_args(argv)
    if args.gui:
        try:
            from app.ui.main_window import run_gui

            return run_gui(args)
        except ImportError:
            print("PySide6 설치 필요: pip install PySide6 (GUI 대신 CLI를 사용하세요).", file=sys.stderr)
            return 1
    return run_cli(args)


if __name__ == "__main__":
    raise SystemExit(main())
