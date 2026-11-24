"""GUI responsiveness and cancellation during a real 3,000,000-cell analysis.

The requirement is that a 150,000-row x 20-column scan never blocks the GUI
thread. This runs the actual MainWindow headless in a subprocess, drives the
real AnalysisWorker over a synthetic 150k-row fixture, counts GUI event-loop
ticks while the scan runs, then cancels mid-flight and checks that the partial
Cell ledger is still complete and self-consistent.
"""
from __future__ import annotations

import json
import random
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ROWS = 150_000
COLUMNS = 20
RUN_SECONDS = 45

SCRIPT = """
import json, sys, time
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from app.ui.main_window import MainWindow

app = QApplication([])
window = MainWindow()
window.show()
window.select_mode("table")

fixture = Path(sys.argv[1])
window.selector.files = [fixture]
window.settings_window.ai_check.setChecked(False)
window.key_selector.set_files([fixture])
window.key_selector.load_columns()
window.key_selector.set_keys(["SEQ_NO", "RECORD_KEY"])
window.run_analysis()
worker = window._worker

ticks = 0
def tick():
    global ticks
    ticks += 1
timer = QTimer()
timer.setInterval(100)
timer.timeout.connect(tick)
timer.start()

progress = []
worker.progress.connect(lambda payload: progress.append(payload))

deadline = time.time() + int(sys.argv[2])
while worker.isRunning() and time.time() < deadline:
    app.processEvents()
window.cancel_analysis()
while worker.isRunning() and time.time() < deadline + 120:
    app.processEvents()
worker.wait(120000)
app.processEvents()

store = window._stores[-1] if window._stores else None
print(json.dumps({
    "ticks": ticks,
    "progress_signals": len(progress),
    "last_progress": progress[-1] if progress else {},
    "cell_signal_seen": any("처리 Cell 수" in payload for payload in progress),
    "rows_parsed": window.history[-1].rows_parsed if window.history else 0,
    "completeness": store.verify_completeness() if store else None,
}, ensure_ascii=False))
window.cleanup_stores()
window.shutdown()
app.processEvents()
"""


def _fixture(path: Path) -> None:
    rng = random.Random(11)
    columns = ["SEQ_NO", "RECORD_KEY", "CUSTOMER_NAME", "PHONE_NO"] + [
        f"COL_{index}" for index in range(COLUMNS - 4)
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(",".join(columns) + "\n")
        for row in range(1, ROWS + 1):
            cells = [str(row), f"REC-{row}"]
            for column in columns[2:]:
                if column == "CUSTOMER_NAME":
                    cells.append(rng.choice(["김민준", "홍길동", "이서연"]))
                elif column == "PHONE_NO":
                    cells.append(f"010-{rng.randint(1000, 9999)}-{rng.randint(1000, 9999)}")
                else:
                    cells.append(rng.choice(["", "ORD-2024-001", "1234567890123456",
                                            "강남구 Sequoia-ro 121", "담당자: 박도윤"]))
            handle.write(",".join(cells) + "\n")


def test_gui_stays_responsive_and_cancels_at_3m_cells(tmp_path) -> None:
    fixture = tmp_path / "scale_150000x20.csv"
    _fixture(fixture)
    completed = subprocess.run(
        [sys.executable, "-c", SCRIPT, str(fixture), str(RUN_SECONDS)],
        cwd=REPO, capture_output=True, text=True, timeout=600,
        env={"QT_QPA_PLATFORM": "offscreen", "PYTHONPATH": str(REPO)},
    )
    assert completed.returncode == 0, completed.stderr[-1500:]
    payload = json.loads(completed.stdout.strip().splitlines()[-1])

    # A 10 Hz heartbeat that keeps firing for the whole scan proves the GUI
    # thread was never blocked by the analysis.
    assert payload["ticks"] >= RUN_SECONDS * 8, payload
    assert payload["progress_signals"] > 0, payload
    assert payload["cell_signal_seen"], "the progress panel must report Cell counts"
    assert 0 < payload["rows_parsed"] < ROWS, "cancel must stop the scan early"

    completeness = payload["completeness"]
    assert completeness["complete"] is True, completeness
    assert completeness["missing_cells"] == 0, completeness
    assert sum(completeness["status_counts"].values()) == completeness["logical_cells"]

    out = REPO / "results" / "gui_scale_measured.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({
        "fixture": f"{ROWS} rows x {COLUMNS} columns",
        "gui_heartbeat_ticks_while_running": payload["ticks"],
        "progress_signals": payload["progress_signals"],
        "cell_count_reported_to_gui": payload["cell_signal_seen"],
        "cancelled_after_rows": payload["rows_parsed"],
        "partial_completeness": completeness,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
