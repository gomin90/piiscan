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

from app.ai import create_ai_engine
from app.ai.gpu_detector import detect_hardware
from app.config.settings import AnalysisConfig
from app.core.analysis_service import analyse_file
from app.export.csv_exporter import write_csv
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
    parser.add_argument("--debug", action="store_true", help="진단 로그 출력")
    parser.add_argument("--json", action="store_true", help="JSON 요약 stdout")
    parser.add_argument("--gui", action="store_true", help="PySide6 GUI 실행")
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
    config.debug = args.debug

    logger = get_logger()
    diagnostics = Diagnostics(enabled=args.debug)
    hardware = detect_hardware()
    if args.cpu_only:
        hardware.backend = "cpu"
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
        logger.info(
            "%s -> 개인정보 유형 %d개, 위험도 높음 %d건 (%.2fs)",
            Path(path).name,
            len(result.results),
            sum(1 for r in result.results if r.risk_level in ("매우 높음", "높음")),
            result.elapsed_seconds,
        )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output_dir)
    flat_results = [row for result in results for row in result.results]
    csv_path = write_csv(flat_results, output_dir / f"privacy_results_{timestamp}.csv")
    txt_path = write_txt(results, output_dir / f"privacy_report_{timestamp}.txt")
    logger.info("결과: %s / %s", csv_path, txt_path)

    if args.debug:
        diagnostics.dump(output_dir / f"diagnostics_{timestamp}.json")

    if args.json:
        print(json.dumps({
            "hardware": hardware.as_dict(),
            "ai": llm.summary(),
            "files": [r.as_dict() for r in results],
        }, ensure_ascii=False, indent=2))
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
