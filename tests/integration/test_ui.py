"""GUI modules must import and build the main window headlessly."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ui.main_window import MainWindow  # noqa: E402


def test_main_window_builds():
    app = QApplication.instance() or QApplication()
    window = MainWindow()
    assert window.analysis_tab is not None
    assert window.selector.files == []
    assert window.progress is not None
    window.close()
