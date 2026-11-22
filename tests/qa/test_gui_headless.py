"""QA: GUI behaviour in a headless (offscreen) Qt session.

Verifies that the settings pane really feeds the analysis engine, that the
diagnostics tab is populated in debug mode, and that GUI-generated exports
contain no raw personal data.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not pytest.importorskip("PySide6", reason="PySide6 required for GUI QA"),
    reason="PySide6 not installed",
)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.config.settings import AnalysisConfig  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402

RAW = ["900101-1234567", "010-1234-5678", "min.kim@example.com", "1234-5678-9012-3456", "R12345678"]


@pytest.fixture()
def app():
    application = QApplication.instance() or QApplication()
    yield application


@pytest.fixture()
def gui_csv(tmp_path: Path) -> Path:
    csv = tmp_path / "customers.csv"
    csv.write_text(
        "NAME,RESIDENT_NO,PHONE,EMAIL,CARD_NO,PASSPORT,ORDER_REF\n"
        "김민준,900101-1234567,010-1234-5678,min.kim@example.com,1234-5678-9012-3456,R12345678,AB12-CD34\n",
        encoding="utf-8",
    )
    return csv


def test_settings_apply_reaches_the_engine(app, gui_csv, tmp_path, monkeypatch):
    window = MainWindow()
    window.settings_window.batch_box.setValue(250)
    window.settings_window.thread_box.setValue(4)
    window.settings_window.debug_check.setChecked(True)
    window.selector.files = [gui_csv]

    monkeypatch.chdir(tmp_path)
    window.run_analysis()

    assert window.config.batch_size == 250
    assert window.config.threads == 4
    assert window.config.debug is True
    assert window.diagnostics, "debug mode must record diagnostics events"
    assert window.debug_label.text().strip() and "디버그 정보 없음" not in window.debug_label.text()


def test_gui_exports_do_not_contain_raw_values(app, gui_csv, tmp_path, monkeypatch):
    window = MainWindow()
    window.selector.files = [gui_csv]
    monkeypatch.chdir(tmp_path)
    window.run_analysis()

    outputs = sorted((tmp_path / "results").glob("*"))
    assert outputs, "GUI run must write report files"
    for path in outputs:
        text = path.read_text(encoding="utf-8", errors="replace")
        for raw in RAW:
            assert raw not in text, f"raw value {raw} leaked into {path.name}"

    csv_files = [p for p in outputs if p.suffix == ".csv"]
    txt_files = [p for p in outputs if p.suffix == ".txt"]
    assert csv_files and txt_files, "both CSV and TXT reports expected"
    assert len(csv_files[0].read_text(encoding="utf-8").splitlines()) > 1


def test_gui_form_fields_reach_the_report(app, gui_csv, tmp_path, monkeypatch):
    """분석 방식 / 테이블명 / 스키마 / 원본 DB 입력이 결과 CSV에 recorded."""
    window = MainWindow()
    window.settings_window.mode_box.setCurrentText("full")
    window.settings_window.table_box.setText("CUSTOMERS")
    window.settings_window.schema_box.setText("PROD")
    window.settings_window.db_box.setText("Oracle")
    window.selector.files = [gui_csv]
    monkeypatch.chdir(tmp_path)
    window.run_analysis()

    csv_path = sorted((tmp_path / "results").glob("privacy_results_*.csv"))[0]
    text = csv_path.read_text(encoding="utf-8-sig")
    assert "CUSTOMERS" in text and "PROD" in text and "Oracle" in text
    assert window.config.mode == "full"


def test_result_detail_shows_row_level_hits(app, gui_csv, tmp_path, monkeypatch):
    window = MainWindow()
    window.selector.files = [gui_csv]
    monkeypatch.chdir(tmp_path)
    window.run_analysis()

    hits = sorted((tmp_path / "results").glob("privacy_hits_*.csv"))
    assert hits, "GUI run must write the row-level hit report"
    text = hits[0].read_text(encoding="utf-8-sig")
    assert "행번호" in text and "탐지값(마스킹)" in text

    first = window.result_window.rows[0]
    window.result_window.show_detail(first)
    assert window.result_window.hit_table.rowCount() > 0
    assert window.result_window.hit_table.item(0, 4).text()  # masked value column


def test_result_window_paginates_row_detail(app, tmp_path, monkeypatch):
    """More rows than the memory cache must still be browsable page by page."""
    csv_path = tmp_path / "many.csv"
    csv_path.write_text(
        "IDX,EMAIL\n" + "\n".join(f"{i},user{i}@example.com" for i in range(1, 601)),
        encoding="utf-8",
    )
    window = MainWindow()
    window.selector.files = [csv_path]
    monkeypatch.chdir(tmp_path)
    window.run_analysis()

    result = window.result_window
    result.show_rows(result._file_results, page=0, page_size=100)
    assert result.row_table.rowCount() == 100
    assert "600건 저장됨" in result.row_context.text()
    assert result.next_page.isEnabled() and not result.prev_page.isEnabled()
    result._change_page(1)
    assert result.row_table.item(0, 0).text() == "101"
    assert result.hit_table.rowCount() > 0
    result._change_page(4)
    assert result.row_table.rowCount() == 100
    assert result.row_table.item(0, 0).text() == "501"
    assert not result.next_page.isEnabled()


def test_debug_off_leaves_diagnostics_empty(app, gui_csv, tmp_path, monkeypatch):
    window = MainWindow()
    window.config.debug = False
    window.settings_window.debug_check.setChecked(False)
    window.selector.files = [gui_csv]
    monkeypatch.chdir(tmp_path)
    window.run_analysis()
    assert window.diagnostics == []
    assert "디버그 정보 없음" in window.debug_label.text()

def test_file_and_folder_buttons_reach_the_choosers(app, gui_csv, tmp_path, monkeypatch):
    """The CSV/folder buttons must actually invoke the file dialogs."""
    from PySide6.QtWidgets import QFileDialog

    from app.ui.file_selector import FileSelector

    calls: list[str] = []

    def fake_open_files(*args, **kwargs):
        calls.append("open_files")
        return [str(gui_csv)], ""

    def fake_directory(*args, **kwargs):
        calls.append("directory")
        return str(tmp_path)

    monkeypatch.setattr(QFileDialog, "getOpenFileNames", staticmethod(fake_open_files))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(fake_directory))

    selector = FileSelector()
    selector.file_button.click()
    assert selector.files == [gui_csv], "CSV button did not register the chosen file"
    assert selector.list_widget.count() == 1

    selector.folder_button.click()
    assert gui_csv in selector.files, "folder button did not register the folder contents"
    assert "선택됨" in selector.info_label.text()
    assert calls == ["open_files", "directory"]
