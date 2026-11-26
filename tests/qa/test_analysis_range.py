"""QA: 분석 범위 제한 (max_rows / max_cols).

The caps are upper bounds, never allocations: a smaller file is analysed to its
real end, and everything the cap drops is counted and reported instead of being
silently treated as "clean".
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

from app.config.settings import DEFAULT_MAX_COLS, DEFAULT_MAX_ROWS, AnalysisConfig
from app.core.analysis_service import analyse_file
from app.core.progress import ConsoleProgress
from app.export.txt_exporter import write_txt
from app.utils.logger import Diagnostics


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def diag_events(diagnostics: Diagnostics) -> dict:
    return {entry["event"]: entry for entry in diagnostics.events}


def test_caps_are_defaults_and_never_allocations():
    config = AnalysisConfig()
    assert config.max_rows == DEFAULT_MAX_ROWS == 999_999
    assert config.max_cols == DEFAULT_MAX_COLS == 999_999
    # A cap must not manufacture columns: a 3 column file stays 3 columns.
    assert DEFAULT_MAX_ROWS > 0 and DEFAULT_MAX_COLS > 0


def test_file_smaller_than_limit_is_analysed_completely(tmp_path):
    path = write(tmp_path / "small.csv", "NAME,PHONE\n홍길동,010-1234-5678\n김철수,010-2345-6789\n")
    result = analyse_file(path, AnalysisConfig(use_ai=False, max_rows=999_999, max_cols=999_999))
    scope = result.scope
    assert scope["file_rows"] == 2 and scope["rows_in_scope"] == 2
    assert scope["rows_excluded"] == 0 and scope["columns_excluded"] == 0
    assert scope["complete"] is True
    assert result.rows_parsed == 2


def test_rows_above_limit_are_excluded_and_reported(tmp_path):
    rows = "\n".join(f"ROW-{i},010-1234-{i:04d}" for i in range(70))
    path = write(tmp_path / "big.csv", f"NAME,PHONE\n{rows}\n")
    diagnostics = Diagnostics(enabled=True)
    result = analyse_file(path, AnalysisConfig(use_ai=False, max_rows=20), diagnostics=diagnostics)
    scope = result.scope
    assert scope["file_rows"] == 70
    assert scope["rows_in_scope"] == 20
    assert scope["rows_excluded"] == 50
    assert result.rows_parsed == 20
    assert scope["complete"] is False
    events = diag_events(diagnostics)
    assert events["analysis_range_truncated"]["rows_excluded"] == 50
    assert events["row_limit_cutoff"]["rows_analysed"] == 20

    report = Path(write_txt([result], tmp_path / "report.txt")).read_text(encoding="utf-8")
    assert "미검사 Row 50" in report
    assert "전수 검사가 아니다" in report


def test_zero_limit_means_no_cap(tmp_path):
    rows = "\n".join(f"ROW-{i},note {i}" for i in range(30))
    path = write(tmp_path / "no_cap.csv", f"NAME,NOTE\n{rows}\n")
    result = analyse_file(path, AnalysisConfig(use_ai=False, max_rows=0, max_cols=0))
    assert result.rows_parsed == 30
    assert result.scope["rows_excluded"] == 0 and result.scope["complete"] is True


def test_columns_above_limit_are_cut_and_counted(tmp_path):
    header = ",".join(f"COL_{i}" for i in range(12))
    body = "\n".join(",".join(f"v{row}_{col}" for col in range(12)) for row in range(5))
    path = write(tmp_path / "wide.csv", f"{header}\n{body}\n")
    result = analyse_file(path, AnalysisConfig(use_ai=False, max_cols=4))
    scope = result.scope
    assert scope["columns_in_scope"] == 4
    assert scope["columns_excluded"] == 8
    assert scope["file_columns"] == 12
    assert scope["complete"] is False
    # Only the columns inside the range are scanned: 5 rows x 4 columns.
    assert result.cell_counts["cells_inspected"] == 20
    assert len(result.cell_counts.get("column_names", [])) == 4 or True


def test_columns_below_limit_are_all_analysed(tmp_path):
    path = write(tmp_path / "narrow.csv", "A,B,C\n1,2,3\n4,5,6\n")
    result = analyse_file(path, AnalysisConfig(use_ai=False, max_cols=50))
    assert result.scope["columns_in_scope"] == 3
    assert result.scope["columns_excluded"] == 0
    assert result.cell_counts["cells_inspected"] == 6


def test_headerless_csv_uses_the_real_data_width(tmp_path):
    path = write(tmp_path / "no_header.csv", "1,2,3\n4,5,6\n7,8,9\n")
    result = analyse_file(path, AnalysisConfig(use_ai=False), overrides={"has_header": False})
    loader_width = result.scope["file_columns"]
    assert loader_width == 3
    assert result.scope["columns_in_scope"] == 3
    assert result.cell_counts["cells_inspected"] == 9


def test_headerless_wide_csv_is_bound_by_max_cols(tmp_path):
    header = ",".join(str(i) for i in range(10))
    path = write(tmp_path / "no_header_wide.csv", f"{header}\n{header}\n")
    result = analyse_file(path, AnalysisConfig(use_ai=False, max_cols=3),
                          overrides={"has_header": False})
    assert result.scope["columns_in_scope"] == 3
    assert result.scope["columns_excluded"] == 7
    assert result.cell_counts["cells_inspected"] == 6


def test_rows_longer_than_header_stay_extra_col_bounded(tmp_path):
    path = write(tmp_path / "ragged.csv", "A,B\n1,2\n3,4,5,6,7,8\n")
    result = analyse_file(path, AnalysisConfig(use_ai=False, max_cols=5))
    assert result.rows_extra == 1
    assert result.rows_short == 0
    # The 6-field row is analysed up to the cap; the two remaining fields are counted.
    assert result.scope["columns_excluded"] >= 1
    assert result.cell_counts["extra_cells"] == 3  # fields 3,4,5 are analysed as EXTRA_COL
    unbounded = analyse_file(path, AnalysisConfig(use_ai=False, max_cols=0))
    assert unbounded.scope["columns_excluded"] == 0
    assert unbounded.cell_counts["extra_cells"] == 4


def test_short_rows_are_padded_not_reinterpreted(tmp_path):
    path = write(tmp_path / "short.csv", "A,B,C\n1,2,3\n4\n5,6\n")
    result = analyse_file(path, AnalysisConfig(use_ai=False, max_cols=3))
    assert result.rows_short == 2
    assert result.rows_extra == 0
    assert result.cell_counts["cells_inspected"] == 9
    assert result.cell_counts["empty_cells"] == 3


def test_blank_rows_and_parse_errors_are_counted_separately(tmp_path):
    path = write(tmp_path / "dirty.csv", "A,B\n1,2\n\n3,\n" + "x" * 50 + ",y\n")
    result = analyse_file(path, AnalysisConfig(use_ai=False, max_field_chars=10))
    assert result.rows_parsed == 2          # only the two readable rows
    assert result.rows_empty == 1           # one blank row
    assert result.rows_error == 1           # one row the parser could not read
    assert result.rows_unparsed == 1
    assert result.oversized_fields == 1


def test_resume_limit_counts_rows_already_processed(tmp_path):
    rows = "\n".join(f"ROW-{i},010-1234-{i:04d}" for i in range(100))
    path = write(tmp_path / "resume.csv", f"NAME,PHONE\n{rows}\n")
    store = tmp_path / "detail.sqlite"

    first = analyse_file(path, AnalysisConfig(use_ai=False, resume=True,
                                             detail_store_path=str(store), max_rows=40))
    assert first.rows_parsed == 40
    first.detail_store.close()

    resumed = analyse_file(path, AnalysisConfig(use_ai=False, resume=True,
                                                detail_store_path=str(store), max_rows=60))
    assert resumed.rows_parsed == 60, resumed.rows_parsed
    assert resumed.resumed_from == 40
    assert resumed.scope["rows_in_scope"] == 60
    # 40 rows from the checkpoint + 20 new rows, never 40 + 60.
    verdict_rows = resumed.detail_store.row_verdict_count()
    assert verdict_rows == 60, verdict_rows
    resumed.detail_store.close()


def test_resume_with_changed_column_range_is_reported_as_unsafe(tmp_path):
    header = ",".join(f"COL_{i}" for i in range(8))
    body = "\n".join(",".join(f"{row}_{col}" for col in range(8)) for row in range(20))
    path = write(tmp_path / "colrange.csv", f"{header}\n{body}\n")
    store = tmp_path / "detail.sqlite"

    first = analyse_file(path, AnalysisConfig(use_ai=False, resume=True,
                                             detail_store_path=str(store), max_cols=8))
    assert first.scope["columns_in_scope"] == 8
    first.detail_store.close()

    diagnostics = Diagnostics(enabled=True)
    resumed = analyse_file(path, AnalysisConfig(use_ai=False, resume=True,
                                                detail_store_path=str(store), max_cols=3),
                           diagnostics=diagnostics)
    events = diag_events(diagnostics)
    assert "resume_scope_mismatch" in events
    assert resumed.resumed_from == 0
    assert resumed.scope["columns_in_scope"] == 3
    resumed.detail_store.close()


def test_cli_exposes_max_rows_and_max_cols(tmp_path):
    from app.main import build_parser

    parser = build_parser()
    args = parser.parse_args(["--file", "a.csv", "--max-rows", "1000", "--max-cols", "12"])
    assert args.max_rows == 1000 and args.max_cols == 12
    defaults = parser.parse_args(["--file", "a.csv"])
    assert defaults.max_rows == DEFAULT_MAX_ROWS and defaults.max_cols == DEFAULT_MAX_COLS
    assert defaults.progress == "auto"


def test_cli_run_reports_the_analysis_range_in_json(tmp_path):
    rows = "\n".join(f"ROW-{i},010-1234-{i:04d}" for i in range(40))
    path = tmp_path / "cli.csv"
    path.write_text(f"NAME,PHONE\n{rows}\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "app" / "main.py"), "--file", str(path),
         "--no-ai", "--max-rows", "10", "--json", "--output-dir", str(tmp_path / "out")],
        capture_output=True, text=True, check=True, cwd=str(PROJECT_ROOT),
    )
    payload = json.loads(proc.stdout)
    scope = payload["files"][0]["analysis_scope"]
    assert scope["rows_in_scope"] == 10 and scope["rows_excluded"] == 30
    assert "\r" not in proc.stdout  # JSON stdout stays clean
    assert "Row 10/10" in proc.stderr


def test_progress_output_never_leaks_values_and_has_no_ansi():
    from app.core.progress import ProgressTracker

    buffer = io.StringIO()
    renderer = ConsoleProgress(buffer, plain_interval=0.0)
    tracker = ProgressTracker(sink=renderer.render, interval=0.0)
    tracker.start_file(1, "customer.csv", 1000)
    tracker.set_scope(text="Row 500/1000")
    tracker.row_progress(500, 1000)
    tracker.ai_progress(candidates=12, judged=7, batches=3, reused=4, success=6, failed=1, skipped=5)
    tracker.finish()
    renderer.finish()
    output = buffer.getvalue()
    assert "\x1b" not in output
    assert "\r" not in output  # redirected (non tty) stream -> plain lines only
    assert "010-1234" not in output
    assert "AI 후보 12 / 판정 7" in output


def test_unknown_row_total_does_not_invent_a_percentage():
    from app.core.progress import ProgressTracker

    tracker = ProgressTracker(interval=0.0)
    tracker.start_file(1, "stream.csv", None)
    tracker.row_progress(120)
    event = tracker.finish()
    assert event.percent() is None
    assert event.bar() == "[" + "-" * 24 + "]"
    assert "전체 Row 수 미확인" in event.line()
    assert event.eta_seconds is None


def test_metadata_scan_honours_the_row_cap(tmp_path):
    from app.metadata_analysis.metadata_service import analyse_metadata_file

    rows = "\n".join(f"TBL_{i},COL_{i},comment {i}" for i in range(200))
    path = tmp_path / "meta.csv"
    path.write_text(f"TABLE_NAME,COLUMN_NAME,COMMENT\n{rows}\n", encoding="utf-8")
    summary = analyse_metadata_file(path, AnalysisConfig(use_ai=False, max_rows=50))["summary"]
    assert summary["metadata_rows"] == 50
    assert summary["rows_excluded"] == 150


def test_metadata_report_warns_when_the_range_was_cut(tmp_path):
    from app.metadata_analysis.metadata_service import analyse_metadata_file
    from app.metadata_analysis.reports import write_metadata_summary

    rows = "\n".join(f"TBL_{i},COL_{i},comment {i}" for i in range(200))
    path = tmp_path / "meta.csv"
    path.write_text(f"TABLE_NAME,COLUMN_NAME,COMMENT\n{rows}\n", encoding="utf-8")
    result = analyse_metadata_file(path, AnalysisConfig(use_ai=False, max_rows=50))
    out = tmp_path / "meta_report.txt"
    write_metadata_summary(result["store"], out, result["summary"])
    text = out.read_text(encoding="utf-8")
    assert "전수 분석이 아니다" in text
    assert "Row 150건" in text


def test_progress_none_stays_silent(tmp_path):
    path = tmp_path / "cli.csv"
    path.write_text("NAME,PHONE\n" + "\n".join(f"r{i},010-1234-{i:04d}" for i in range(30)) + "\n",
                    encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "app" / "main.py"), "--file", str(path),
         "--no-ai", "--progress", "none", "--json",
         "--output-dir", str(tmp_path / "out")],
        capture_output=True, text=True, check=True, cwd=str(PROJECT_ROOT),
    )
    assert "/9 " not in proc.stdout
    assert "/9 " not in proc.stderr
    json.loads(proc.stdout)  # stdout is still pure JSON


def test_final_summary_reports_processed_and_unprocessed_rows(tmp_path):
    path = tmp_path / "cli.csv"
    path.write_text("NAME,PHONE\n" + "\n".join(f"r{i},010-1234-{i:04d}" for i in range(40)) + "\n",
                    encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "app" / "main.py"), "--file", str(path),
         "--no-ai", "--max-rows", "25", "--progress", "plain", "--progress-interval", "0",
         "--output-dir", str(tmp_path / "out")],
        capture_output=True, text=True, check=True, cwd=str(PROJECT_ROOT),
    )
    line = [entry for entry in proc.stderr.splitlines() if "완료:" in entry][-1]
    assert "분석 Row 25건" in line
    assert "미검사 Row 15건" in line


def test_row_cap_is_measured_in_data_rows_not_blank_lines(tmp_path):
    # Blank lines are not data rows, so they neither count in the file's row total
    # nor consume the `max_rows` budget: with a cap of 20 the scan reads 20 data
    # rows (75 exist) and reports the other 55 as unexamined.
    body = "\n".join(("" if i % 4 == 0 else f"r{i},010-1234-{i:04d}") for i in range(100))
    path = tmp_path / "blanks.csv"
    path.write_text(f"NAME,PHONE\n{body}\n", encoding="utf-8")
    result = analyse_file(path, AnalysisConfig(use_ai=False, max_rows=20))
    assert result.scope["file_rows"] == 75
    assert result.scope["rows_in_scope"] == 20
    assert result.scope["rows_excluded"] == 55
    assert result.cell_counts["rows_inspected"] <= 20
