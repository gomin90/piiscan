"""End-to-end pipeline: file in -> CSV/TXT report out."""
from __future__ import annotations

from pathlib import Path

from app.core.analysis_service import analyse_file
from app.export.csv_exporter import write_csv
from app.export.txt_exporter import write_txt
from app.main import build_parser, collect_files
from tests.fixtures.generate_fixtures import FIXTURE_DIR, generate


def test_full_pipeline_writes_reports(tmp_path: Path):
    generate()
    result = analyse_file(FIXTURE_DIR / "utf8_comma.csv")
    assert result.rows_parsed == 8
    csv_path = write_csv([r.as_dict() for r in result.results], tmp_path / "report.csv")
    txt_path = write_txt([result], tmp_path / "report.txt")

    csv_text = Path(csv_path).read_text(encoding="utf-8-sig")
    assert "주민등록번호" in csv_text
    assert "매우 높음" in csv_text

    txt_text = Path(txt_path).read_text(encoding="utf-8")
    assert "분석 보고서" in txt_text
    assert "추가 확인" in txt_text or "위험도" in txt_text


def test_report_contains_no_raw_personal_data(tmp_path: Path):
    generate()
    result = analyse_file(FIXTURE_DIR / "utf8_comma.csv")
    write_txt([result], tmp_path / "report.txt")
    write_csv([r.as_dict() for r in result.results], tmp_path / "report.csv")
    text = (tmp_path / "report.txt").read_text(encoding="utf-8-sig") + (tmp_path / "report.csv").read_text(encoding="utf-8-sig")
    # Fixture values must not leak into the report.
    for token in ("user1@example.com", "010-"):
        assert token not in text, f"raw personal data leaked: {token}"


def test_cli_collects_folder_files(tmp_path: Path):
    generate()
    parser = build_parser()
    args = parser.parse_args(["--folder", str(FIXTURE_DIR), "--output-dir", str(tmp_path), "--no-ai"])
    files = collect_files(args)
    assert len(files) >= 18


def test_sampling_mode_flag_for_small_files():
    generate()
    result = analyse_file(FIXTURE_DIR / "utf8_comma.csv")
    assert result.settings["mode"].startswith("sampling")
