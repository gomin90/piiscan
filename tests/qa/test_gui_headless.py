"""QA: GUI behaviour in a headless (offscreen) Qt session.

Verifies that the settings pane really feeds the analysis engine, that the
diagnostics tab is populated in debug mode, that GUI-generated exports contain
no raw personal data, and that the widget graph is handed back to Qt before the
process exits (PySide 6.12 aborts with a libmalloc double free otherwise).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not pytest.importorskip("PySide6", reason="PySide6 required for GUI QA"),
    reason="PySide6 not installed",
)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.config.settings import AnalysisConfig  # noqa: E402
from app.ui.lifecycle import close_widgets  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402

RAW = ["900101-1234567", "010-1234-5678", "min.kim@example.com", "1234-5678-9012-3456", "R12345678"]


@pytest.fixture()
def app():
    application = QApplication.instance() or QApplication()
    yield application


@pytest.fixture()
def gui_window(app):
    """A MainWindow that is handed back to Qt before the process exits.

    PySide aborts if the widget graph is garbage-collected while the application
    is still alive (see app/ui/lifecycle.py).
    """
    window = MainWindow()
    yield window
    close_widgets([window])


@pytest.fixture()
def gui_csv(tmp_path: Path) -> Path:
    csv = tmp_path / "customers.csv"
    csv.write_text(
        "NAME,RESIDENT_NO,PHONE,EMAIL,CARD_NO,PASSPORT,ORDER_REF\n"
        "김민준,900101-1234567,010-1234-5678,min.kim@example.com,1234-5678-9012-3456,R12345678,AB12-CD34\n",
        encoding="utf-8",
    )
    return csv


def test_settings_apply_reaches_the_engine(app, gui_window, gui_csv, tmp_path, monkeypatch):
    window = gui_window
    window.settings_window.batch_box.setValue(250)
    window.settings_window.thread_box.setValue(4)
    window.settings_window.debug_check.setChecked(True)
    window.selector.files = [gui_csv]

    monkeypatch.chdir(tmp_path)
    window.run_analysis()
    window.wait_for_analysis(120_000)

    assert window.config.batch_size == 250
    assert window.config.threads == 4
    assert window.config.debug is True
    assert window.diagnostics, "debug mode must record diagnostics events"
    assert window.debug_label.text().strip() and "디버그 정보 없음" not in window.debug_label.text()


def test_gui_exports_do_not_contain_raw_values(app, gui_window, gui_csv, tmp_path, monkeypatch):
    window = gui_window
    window.selector.files = [gui_csv]
    monkeypatch.chdir(tmp_path)
    window.run_analysis()
    window.wait_for_analysis(120_000)

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


def test_gui_form_fields_reach_the_report(app, gui_window, gui_csv, tmp_path, monkeypatch):
    """분석 방식 / 테이블명 / 스키마 / 원본 DB 입력이 결과 CSV에 recorded."""
    window = gui_window
    window.settings_window.mode_box.setCurrentText("full")
    window.settings_window.table_box.setText("CUSTOMERS")
    window.settings_window.schema_box.setText("PROD")
    window.settings_window.db_box.setText("Oracle")
    window.selector.files = [gui_csv]
    monkeypatch.chdir(tmp_path)
    window.run_analysis()
    window.wait_for_analysis(120_000)

    csv_path = sorted((tmp_path / "results").glob("privacy_results_*.csv"))[0]
    text = csv_path.read_text(encoding="utf-8-sig")
    assert "CUSTOMERS" in text and "PROD" in text and "Oracle" in text
    assert window.config.mode == "full"


def test_result_detail_shows_row_level_hits(app, gui_window, gui_csv, tmp_path, monkeypatch):
    window = gui_window
    window.selector.files = [gui_csv]
    monkeypatch.chdir(tmp_path)
    window.run_analysis()
    window.wait_for_analysis(120_000)

    hits = sorted((tmp_path / "results").glob("privacy_hits_*.csv"))
    assert hits, "GUI run must write the row-level hit report"
    text = hits[0].read_text(encoding="utf-8-sig")
    assert "행번호" in text and "탐지값(마스킹)" in text

    first = window.result_window.rows[0]
    window.result_window.show_detail(first)
    assert window.result_window.hit_model.rowCount() > 0
    assert window.result_window.hit_model.records[0]["탐지값(마스킹)"]  # masked value column


def test_result_window_paginates_row_detail(app, gui_window, tmp_path, monkeypatch):
    """More rows than the memory cache must still be browsable page by page."""
    csv_path = tmp_path / "many.csv"
    csv_path.write_text(
        "IDX,EMAIL\n" + "\n".join(f"{i},user{i}@example.com" for i in range(1, 601)),
        encoding="utf-8",
    )
    window = gui_window
    window.selector.files = [csv_path]
    monkeypatch.chdir(tmp_path)
    window.run_analysis()
    window.wait_for_analysis(120_000)

    result = window.result_window
    result.show_rows(result._file_results, page=0, page_size=100)
    assert result.row_model.rowCount() == 100
    assert "600건" in result.row_context.text()
    assert result.next_page.isEnabled() and not result.prev_page.isEnabled()
    result._change_page(1)
    assert result.row_model.records[0]["행번호"] == 101
    # Cell detail is loaded only for the Row the user selects, capped at one page.
    result.select_row(result._row_page[0])
    assert result.hit_model.rowCount() > 0
    assert result.hit_model.rowCount() <= result.cell_page_size
    result._change_page(4)
    assert result.row_model.rowCount() == 100
    assert result.row_model.records[0]["행번호"] == 501
    assert not result.next_page.isEnabled()


def test_debug_off_leaves_diagnostics_empty(app, gui_window, gui_csv, tmp_path, monkeypatch):
    window = gui_window
    window.config.debug = False
    window.settings_window.debug_check.setChecked(False)
    window.selector.files = [gui_csv]
    monkeypatch.chdir(tmp_path)
    window.run_analysis()
    window.wait_for_analysis(120_000)
    assert window.diagnostics == []
    assert "디버그 정보 없음" in window.debug_label.text()

def test_file_and_folder_buttons_reach_the_choosers(app, gui_window, gui_csv, tmp_path, monkeypatch):
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
    close_widgets(selector)


def test_gui_teardown_does_not_abort_the_interpreter() -> None:
    """PySide 6.12 + CPython 3.15 aborts when widgets die before the app.

    Regression test for the macOS crash report
    `___BUG_IN_CLIENT_OF_LIBMALLOC_POINTER_BEING_FREED_WAS_NOT_ALLOCATED` raised
    inside `PySide::destructionVisitor` at interpreter exit. Without the
    `close_widgets()` teardown this exact widget pattern aborted in roughly one
    out of five processes; with it the pattern is clean over 60 runs.
    """
    script = (
        "import os\n"
        "os.environ['QT_QPA_PLATFORM'] = 'offscreen'\n"
        "from PySide6.QtWidgets import QApplication\n"
        "from app.config.settings import AnalysisConfig\n"
        "from app.ui.lifecycle import close_widgets\n"
        "from app.ui.main_window import MainWindow\n"
        "from app.ui.result_window import ResultWindow\n"
        "app = QApplication([])\n"
        "for _ in range(3):\n"
        "    window = MainWindow()\n"
        "    detail = ResultWindow(AnalysisConfig(use_ai=False))\n"
        "    detail.show_rows([])\n"
        "    close_widgets([window, detail])\n"
        "print('done')\n"
    )
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    failures = []
    for _ in range(6):
        proc = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, env=env
        )
        if proc.returncode != 0 or "shared QObject" in proc.stderr:
            failures.append(proc.returncode)
    assert not failures, f"Qt teardown aborted {len(failures)}/6 runs: {failures}"


def test_analysis_runs_off_the_gui_thread(app, gui_window, gui_csv, tmp_path, monkeypatch):
    """The scan must not block the widget thread: a worker thread does the work."""
    from PySide6.QtCore import QThread

    window = gui_window
    window.selector.files = [gui_csv]
    monkeypatch.chdir(tmp_path)
    window.run_analysis()
    worker = window._worker
    assert worker is not None, "run_analysis must start a background worker"
    assert worker is not QThread.currentThread(), "analysis must not run in the GUI thread"
    # The window is still usable while the worker runs.
    window.progress.update_state(**{"분석 상태": "running"})
    assert window.progress.labels["분석 상태"].text() == "running"
    window.wait_for_analysis(120_000)
    assert not worker.isRunning()
    assert window.history, "the finished result must be recorded"


def test_analysis_can_be_cancelled(app, gui_window, tmp_path, monkeypatch):
    """A cancel request stops the scan and keeps the partial result."""
    csv_path = tmp_path / "big.csv"
    csv_path.write_text(
        "IDX,EMAIL\n" + "\n".join(f"{i},user{i}@example.com" for i in range(1, 20001)),
        encoding="utf-8",
    )
    window = gui_window
    window.settings_window.batch_box.setValue(200)
    window.settings_window.ai_check.setChecked(False)
    window.settings_window.debug_check.setChecked(True)
    window.selector.files = [csv_path]
    monkeypatch.chdir(tmp_path)
    window.run_analysis()
    window.cancel_analysis()
    results = window.wait_for_analysis(120_000)
    assert results, "cancelling still returns the work done so far"
    result = results[0]
    assert result.rows_parsed < 20000, "the scan must stop before the whole file"
    assert any(event["event"] == "analysis_cancelled" for event in window.diagnostics), \
        "the cancellation must be recorded in diagnostics"


def test_cell_ledger_pages_and_filters(app, gui_window, tmp_path, monkeypatch):
    """Every cell gets a verdict row that the GUI can page and filter."""
    csv_path = tmp_path / "cells.csv"
    csv_path.write_text(
        "NAME,AMOUNT,NOTE\n"
        + "\n".join(f"kim{i},{i},{'true' if i % 2 else ''}" for i in range(1, 501)),
        encoding="utf-8",
    )
    window = gui_window
    window.settings_window.ai_check.setChecked(False)
    window.selector.files = [csv_path]
    monkeypatch.chdir(tmp_path)
    window.run_analysis()
    window.wait_for_analysis(120_000)
    result = window.result_window
    result.show_rows(window.history)

    result._show_cell_page(0)
    total = window.history[0].cell_counts["verdict_cells"]
    assert total == 500 * 3, "one verdict record per analysed cell"
    assert result.cell_model.rowCount() == result.cell_page_size
    assert "Cell 판정" in result.cell_context.text()
    result._show_cell_page(1)
    assert result.prev_cell_page.isEnabled()
    # Filter down to the cells the rules positively cleared.
    result.status_filter.setCurrentText("비개인정보")
    result._show_cell_page(0)
    statuses = {row["Cell 판정"] for row in result.cell_model.records}
    assert statuses == {"비개인정보"}, statuses

def test_mode_selector_keeps_the_two_functions_separate(app, gui_window):
    """Function A and Function B are distinct flows with their own entry points."""
    assert gui_window.config.analysis_kind == "table"
    gui_window.select_mode("metadata")
    assert gui_window.config.analysis_kind == "metadata"
    assert gui_window.tabs.currentWidget() is gui_window.metadata_window
    gui_window.select_mode("table")
    assert gui_window.config.analysis_kind == "table"
    assert gui_window.tabs.currentWidget() is gui_window.analysis_tab
    assert gui_window.metadata_window.selector.files == []


def test_key_selector_selection_reaches_the_engine(app, gui_window, tmp_path, monkeypatch):
    """User-selected KEY columns are excluded from cell analysis, never inferred."""
    csv_path = tmp_path / "records.csv"
    csv_path.write_text(
        "SEQ_NO,NAME,PHONE,NOTE\n"
        "1,김민준,010-1234-5678,notes\n"
        "2,이지현,900101-1234567,notes\n",
        encoding="utf-8",
    )
    gui_window.selector.files = [csv_path]
    gui_window.key_selector.set_files([csv_path])
    columns = gui_window.key_selector.load_columns()
    assert columns == ["SEQ_NO", "NAME", "PHONE", "NOTE"]
    gui_window.key_selector.set_keys(["SEQ_NO"])
    assert gui_window.key_selector.selected_keys() == ["SEQ_NO"]

    monkeypatch.chdir(tmp_path)
    gui_window.run_analysis()
    gui_window.wait_for_analysis(120_000)
    assert gui_window.config.key_columns == ["SEQ_NO"]
    counts = gui_window.result_window.status_breakdown()
    assert counts.get("KEY 제외") == 2, "each ROW must record its excluded KEY cell"
    assert counts.get("개인정보") >= 2
    assert sum(counts.values()) == 8  # 2 rows x 4 columns, nothing missing


def test_metadata_analysis_runs_and_exports_from_the_gui(app, gui_window, tmp_path, monkeypatch):
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "metadata_5k.csv"
    window = gui_window
    window.metadata_window.selector.files = [fixture]
    window.metadata_window.detect_mapping()
    assert window.metadata_window.mapping_table.rowCount() > 0
    monkeypatch.chdir(tmp_path)
    window.metadata_window.run_metadata_analysis()
    window.metadata_window.wait_for_analysis(120_000)

    summary = window.metadata_window.summary
    assert summary["metadata_rows"] == 5000
    assert summary["columns"] == 5000
    assert summary["relevant_columns"] > 0
    paths = window.metadata_window.export_reports()
    for path in paths:
        assert path.exists(), f"{path.name} missing"
        text = path.read_text(encoding="utf-8", errors="replace")
        assert len(text.splitlines()) > 1
    columns_file = paths[0]
    lines = columns_file.read_text(encoding="utf-8-sig").splitlines()
    assert "verdict" in lines[0] and len(lines) - 1 == 5000


def test_result_window_shows_record_and_group_views(app, gui_window, tmp_path, monkeypatch):
    csv_path = tmp_path / "grouped.csv"
    rows = ["SEQ_NO,EMAIL,PHONE"]
    for i in range(1, 12):
        rows.append(f"{i},same@example.com,010-1234-{i:04d}")
    csv_path.write_text("\n".join(rows), encoding="utf-8")
    window = gui_window
    window.selector.files = [csv_path]
    window.key_selector.set_files([csv_path])
    window.key_selector.load_columns()
    window.key_selector.set_keys(["SEQ_NO"])
    monkeypatch.chdir(tmp_path)
    window.run_analysis()
    window.wait_for_analysis(120_000)

    result = window.result_window
    assert result.group_model.rowCount() > 0
    repeated = [r for r in result.group_model.records if int(r["발생"]) > 1]
    assert repeated, "the identical-value group must be listed"
    assert repeated[0]["마스킹 샘플"] and "@" in repeated[0]["마스킹 샘플"]
    assert "*" in repeated[0]["마스킹 샘플"], "group samples must stay masked"
    assert result.record_model.rowCount() == 11
    outputs = sorted((tmp_path / "results").glob("privacy_records_*.csv"))
    assert outputs, "record report must be written"
    assert sorted((tmp_path / "results").glob("privacy_groups_*.csv")), "group report must be written"
