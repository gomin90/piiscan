"""GUI shutdown must join worker threads.

PySide 6.12 aborts the process (`QThread: Destroyed while thread is still
running`) when a QThread object is destructed while its thread is still
executing. Every worker the UI starts is therefore registered in
`app.ui.lifecycle` and joined at window close and at interpreter exit. The
subprocess cases make the abort observable as a signal death.

Note: `QThread.sleep()` is unreliable in this PySide build (it can block far
longer than requested), so these tests use `time.sleep`.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

RUNNING_WORKER = """
import os, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from app.config.settings import AnalysisConfig
from app.ui.analysis_worker import MetadataWorker


class SlowWorker(MetadataWorker):
    def run(self) -> None:
        time.sleep(1.5)


app = QApplication([])
worker = SlowWorker([], AnalysisConfig(use_ai=False), None)
worker.start()
time.sleep(0.2)
print("running:", worker.isRunning(), flush=True)
"""

# Same scenario without the lifecycle registration: this is what an unguarded
# worker looks like at teardown, and Qt must abort it. The abort is proof that
# the failure mode is real, but it also writes a macOS crash report that looks
# like a product crash, so the control case only runs on explicit request
# (PIISCAN_TEARDOWN_CONTROL=1). The guarded case above is always exercised.
UNGUARDED_WORKER = """
import os, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication


class TeardownControlWorker(QThread):
    def run(self) -> None:
        time.sleep(1.5)


app = QApplication([])
worker = TeardownControlWorker()
worker.start()
time.sleep(0.2)
print("running:", worker.isRunning(), flush=True)
"""


def _run(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=REPO,
        timeout=60,
    )


def test_close_joins_metadata_worker():
    """Closing the metadata window joins its running worker instead of killing it."""
    from PySide6.QtWidgets import QApplication
    from app.config.settings import AnalysisConfig
    from app.ui.lifecycle import live_threads
    from app.ui.metadata_window import MetadataWindow

    app = QApplication.instance() or QApplication([])
    window = MetadataWindow(AnalysisConfig(use_ai=False), None)
    window.selector.files = [REPO / "tests/fixtures/metadata_5k.csv"]
    window.run_metadata_analysis()
    worker = window._worker
    assert worker is not None
    window.shutdown()
    assert not worker.isRunning()
    assert window._worker is None
    assert live_threads() == []
    window.close()
    app.processEvents()


def test_exit_with_running_worker_is_clean():
    """Interpreter exit while a worker runs must not abort the process."""
    result = _run(RUNNING_WORKER)
    assert "running: True" in result.stdout
    assert "Destroyed while thread" not in result.stderr
    assert result.returncode == 0


def test_unregistered_worker_still_aborts():
    """Control: Qt really does abort an unguarded running QThread.

    Gated because the abort deliberately kills the process and macOS then
    writes a crash report that is indistinguishable from a product crash.
    """
    if not os.environ.get("PIISCAN_TEARDOWN_CONTROL"):
        pytest.skip("set PIISCAN_TEARDOWN_CONTROL=1 to run the deliberate-abort control")
    result = _run(UNGUARDED_WORKER)
    assert "running: True" in result.stdout
    assert "Destroyed while thread" in result.stderr
    assert result.returncode != 0


def test_cancel_cooperatively_stops_worker():
    """Cancellation is cooperative: the worker exits on its own, not by killing."""
    import time

    from PySide6.QtWidgets import QApplication
    from app.config.settings import AnalysisConfig
    from app.ui.analysis_worker import MetadataWorker

    class SlowWorker(MetadataWorker):
        def run(self) -> None:
            time.sleep(1.0)

    app = QApplication.instance() or QApplication([])
    worker = SlowWorker([], AnalysisConfig(use_ai=False), None)
    worker.start()
    worker.request_cancel()
    assert worker.wait(10_000)
    assert not worker.isRunning()
