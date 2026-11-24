"""Every way the user can leave the GUI must exit cleanly.

PySide 6.12 aborts when a QThread object is destroyed while its thread is still
running, so each scenario runs in its own subprocess (a signal death is invisible
inside the pytest process) and must exit with rc 0. All run headless through
QT_QPA_PLATFORM=offscreen.
"""
from __future__ import annotations

import csv
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
META_FIXTURE = REPO / "tests/fixtures/metadata_5k.csv"
ROWS = 40_000

OPEN_WINDOW = """
from PySide6.QtWidgets import QApplication
from app.ui.main_window import MainWindow

app = QApplication([])
window = MainWindow()
window.show()
app.processEvents()
window.close()
app.processEvents()
"""

FUNCTION_A_START = """
from pathlib import Path
from PySide6.QtWidgets import QApplication
from app.ui.main_window import MainWindow

app = QApplication([])
window = MainWindow()
window.show()
window.selector.files = [Path({fixture!r})]
window.key_selector.set_files(window.selector.files)
window.key_selector.load_columns()
window.key_selector.set_keys(["SEQ_NO"])
window.run_analysis()
"""


def _table_fixture(path: Path) -> Path:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["SEQ_NO", "USER_NAME", "PHONE", "EMAIL"])
        for i in range(ROWS):
            writer.writerow([i, "홍길동", "010-1234-5678", "kim@example.com"])
    return path


def _run(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=REPO,
        env={"QT_QPA_PLATFORM": "offscreen", "PYTHONPATH": str(REPO)},
        timeout=180,
    )


def _assert_clean(result: subprocess.CompletedProcess, scenario: str) -> None:
    assert "Destroyed while thread" not in result.stderr, (
        f"{scenario}: Qt aborted a running worker\n{result.stderr[-400:]}"
    )
    assert result.returncode == 0, (
        f"{scenario}: exit code {result.returncode}\n{result.stderr[-400:]}"
    )


def test_closing_the_main_window_exits_cleanly() -> None:
    """1. Open the real MainWindow, then close it the way a user would."""
    _assert_clean(_run(OPEN_WINDOW), "close MainWindow")


def test_exit_without_close_event_while_function_a_runs() -> None:
    """2. The interpreter exits while a Function A worker is still running."""
    with tempfile.TemporaryDirectory() as holder:
        fixture = _table_fixture(Path(holder) / "data.csv")
        script = FUNCTION_A_START.format(fixture=str(fixture)) + """
print("worker running:", window._worker.isRunning(), flush=True)
"""
        result = _run(script)
        _assert_clean(result, "exit while Function A worker runs")
        assert "worker running: True" in result.stdout


def test_shutdown_cancels_and_joins_function_a() -> None:
    """3. shutdown() during a Function A analysis cancels and joins it."""
    with tempfile.TemporaryDirectory() as holder:
        fixture = _table_fixture(Path(holder) / "data.csv")
        script = FUNCTION_A_START.format(fixture=str(fixture)) + """
from app.ui.lifecycle import live_threads
window.shutdown()
print("worker running after shutdown:",
      window._worker is not None and window._worker.isRunning())
print("live threads:", len(live_threads()))
window.close()
app.processEvents()
"""
        result = _run(script)
        _assert_clean(result, "shutdown during Function A")
        assert "worker running after shutdown: False" in result.stdout
        assert "live threads: 0" in result.stdout


def test_shutdown_joins_function_b_metadata_worker() -> None:
    """4. shutdown() during a Function B metadata analysis joins the worker."""
    assert META_FIXTURE.is_file()
    script = f"""
from pathlib import Path
from PySide6.QtWidgets import QApplication
from app.config.settings import AnalysisConfig
from app.ui.lifecycle import live_threads
from app.ui.metadata_window import MetadataWindow

app = QApplication([])
window = MetadataWindow(AnalysisConfig(use_ai=False), None)
window.show()
window.selector.files = [Path({str(META_FIXTURE)!r})]
window.run_metadata_analysis()
print("worker running:", window._worker.isRunning())
window.shutdown()
print("worker running after shutdown:",
      window._worker is not None and window._worker.isRunning())
print("live threads:", len(live_threads()))
window.close()
app.processEvents()
"""
    result = _run(script)
    _assert_clean(result, "shutdown during Function B")
    assert "worker running: True" in result.stdout
    assert "worker running after shutdown: False" in result.stdout
    assert "live threads: 0" in result.stdout


def test_cancel_then_close_exits_cleanly() -> None:
    """5. Cancel mid-analysis, keep using the UI, then close."""
    with tempfile.TemporaryDirectory() as holder:
        fixture = _table_fixture(Path(holder) / "data.csv")
        script = FUNCTION_A_START.format(fixture=str(fixture)) + """
from app.ui.lifecycle import live_threads
window.cancel_analysis()
# The GUI stays usable while the worker drains its current batch.
window.select_mode("metadata")
window.select_mode("table")
window.close()
app.processEvents()
print("live threads at exit:", len(live_threads()))
"""
        result = _run(script)
        _assert_clean(result, "cancel then close")
        assert "live threads at exit: 0" in result.stdout
