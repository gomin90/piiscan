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


def test_debug_off_leaves_diagnostics_empty(app, gui_csv, tmp_path, monkeypatch):
    window = MainWindow()
    window.config.debug = False
    window.settings_window.debug_check.setChecked(False)
    window.selector.files = [gui_csv]
    monkeypatch.chdir(tmp_path)
    window.run_analysis()
    assert window.diagnostics == []
    assert "디버그 정보 없음" in window.debug_label.text()
