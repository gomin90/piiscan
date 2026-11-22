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
from app.config.settings import AnalysisConfig
from app.core.analysis_service import analyse_file
from app.export.csv_exporter import write_csv
from app.export.hit_exporter import write_hit_rows, write_row_records
from app.export.txt_exporter import write_txt
from app.utils.logger import Diagnostics, get_logger


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="폐쇄망 독립형 개인정보 탐지 및 분석 시스템")
    parser.add_argument("--file", action="append", default=[], help="분석할 CSV 파일 (반복 가능)")
    parser.add_argument("--files", nargs="*", default=[], help="분석할 CSV 파일 목록")
    parser.add_argument("--folder", help="폴더 단위 분석 (CSV만 선택)")
    parser.add_argument("--output-dir", default="results", help="결과 CSV/TXT 저장 위치")
    parser.add_argument("--mode", choices=["auto", "sampling", "full"], default="auto")
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
    parser.add_argument("--max-ai-column-reviews", type=int, default=200, help="AI 컬럼 판단 최대 횟수")
    parser.add_argument("--ai-column-reviews", choices=["selective", "all", "none"], default="selective",
                        help="AI 컬럼 판단 범위: 규칙으로 확정된 컬럼 skipping(selective) / 전부(all) / 없음(none)")
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

    llm = create_ai_engine(config, hardware)
    results = []
    for path in files:
        overrides = {}
        if args.encoding:
            overrides["encoding"] = args.encoding
        if args.delimiter:
            overrides["delimiter"] = args.delimiter if args.delimiter != "\\t" else "\t"
        if args.header:
            overrides["has_header"] = args.header == "yes"
        result = analyse_file(path, config, llm, diagnostics=diagnostics, overrides=overrides)
        results.append(result)
        counts = result.cell_counts
        logger.info(
            "%s -> Row %d / Cell %d 전수 검사, 개인정보 Row %d건, 컬럼-유형 %d건, "
            "위험도 높음 %d건 (%.2fs)",
            Path(path).name,
            counts.get("rows_inspected", 0),
            counts.get("cells_inspected", 0),
            len(result.row_records),
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
    csv_path = write_csv(flat_results, output_dir / f"privacy_results_{timestamp}.csv")
    txt_path = write_txt(results, output_dir / f"privacy_report_{timestamp}.txt")
    hits_path = write_hit_rows(results, output_dir / f"privacy_hits_{timestamp}.csv")
    rows_path = write_row_records(results, output_dir / f"privacy_rows_{timestamp}.csv")
    logger.info("결과: %s / %s / %s / %s", csv_path, txt_path, hits_path, rows_path)
    for result in results:
        store = getattr(result, "detail_store", None)
        if store is not None:
            store.close()

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
