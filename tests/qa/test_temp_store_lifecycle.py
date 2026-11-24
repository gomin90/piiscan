"""Temporary detail-store lifecycle: nothing may be left behind.

A run that crashes, is cancelled, or is killed used to leave a multi-gigabyte
``piiscan-detail-*`` directory in the system temp folder. These tests cover every
exit path: normal completion, a forgotten cleanup, an exception mid-analysis, a
duplicate cleanup, SIGTERM, and a kill that no handler can catch (recovered by
the next run's orphan sweep).
"""
from __future__ import annotations

import csv
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from app.core import temp_store
from app.config.settings import AnalysisConfig
from app.core.analysis_service import analyse_file

REPO = Path(__file__).resolve().parents[2]
BASE = Path(tempfile.gettempdir())


def _fixture(path: Path, rows: int = 50) -> Path:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["SEQ_NO", "USER_NAME", "PHONE"])
        for i in range(rows):
            writer.writerow([i, "홍길동", "010-1234-5678"])
    return path


def _detail_dirs() -> set[str]:
    return {str(p) for p in BASE.iterdir() if p.name.startswith(temp_store.PREFIX)}


def _new_detail_dirs(before: set[str]) -> set[str]:
    return _detail_dirs() - before


def test_temp_store_is_removed_after_a_normal_run(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path / "data.csv")
    result = analyse_file(fixture, AnalysisConfig(use_ai=False))
    store = result.detail_store
    temp_dir = store.temp_dir
    assert temp_dir is not None and temp_dir.exists()
    store.cleanup()
    assert not temp_dir.exists()
    assert not any(temp_dir.glob("*"))


def test_closing_without_cleanup_still_removes_the_store() -> None:
    """`atexit` catches a process that never calls cleanup()."""
    before = _detail_dirs()
    with tempfile.TemporaryDirectory() as holder:
        fixture = _fixture(Path(holder) / "data.csv")
        script = f"""
import sys
sys.path.insert(0, {str(REPO)!r})
from pathlib import Path
from app.config.settings import AnalysisConfig
from app.core.analysis_service import analyse_file
analyse_file(Path({str(fixture)!r}), AnalysisConfig(use_ai=False))
"""
        subprocess.run([sys.executable, "-c", script], check=True, timeout=180)
        assert not _new_detail_dirs(before), "temporary stores survived a normal exit"


def test_exception_mid_analysis_does_not_leak_a_store() -> None:
    before = _detail_dirs()
    with tempfile.TemporaryDirectory() as holder:
        fixture = _fixture(Path(holder) / "data.csv")
        script = f"""
import sys
sys.path.insert(0, {str(REPO)!r})
from pathlib import Path
from app.config.settings import AnalysisConfig
from app.core import analysis_service
from app.core.privacy_engine import PrivacyEngine


class Boom(PrivacyEngine):
    def process_batch(self, *args, **kwargs):
        raise RuntimeError("forced engine failure")


analysis_service.PrivacyEngine = Boom
try:
    analysis_service.analyse_file(Path({str(fixture)!r}), AnalysisConfig(use_ai=False))
except Exception as exc:
    print("raised", exc)
"""
        result = subprocess.run([sys.executable, "-c", script], capture_output=True,
                                text=True, timeout=180)
        assert "forced engine failure" in result.stdout
        assert result.returncode == 0
        assert not _new_detail_dirs(before), "an aborted analysis leaked a temp store"


def test_duplicate_cleanup_is_safe() -> None:
    with tempfile.TemporaryDirectory() as holder:
        fixture = _fixture(Path(holder) / "data.csv")
        result = analyse_file(fixture, AnalysisConfig(use_ai=False))
        store = result.detail_store
        first = store.cleanup()
        second = store.cleanup()
        assert first["detail_store_removed"] and second["detail_store_removed"]
        assert not store.temp_dir.exists()


def test_sigterm_cleans_open_stores() -> None:
    """A process interrupted by the OS still releases its database."""
    before = _detail_dirs()
    with tempfile.TemporaryDirectory() as holder:
        fixture = _fixture(Path(holder) / "data.csv", rows=40000)
        script = f"""
import sys
sys.path.insert(0, {str(REPO)!r})
from pathlib import Path
from app.config.settings import AnalysisConfig
from app.core.analysis_service import analyse_file
analyse_file(Path({str(fixture)!r}), AnalysisConfig(use_ai=False))
"""
        proc = subprocess.Popen([sys.executable, "-c", script],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # Wait until the store exists, then interrupt the analysis mid-flight.
        deadline = time.time() + 30
        while time.time() < deadline and not _new_detail_dirs(before):
            time.sleep(0.2)
        proc.send_signal(signal.SIGTERM)
        # Generous: the child imports the whole engine, and this suite often runs
        # alongside the scale benchmarks on the same machine.
        proc.wait(timeout=300)
        assert proc.returncode == 0
        assert not _new_detail_dirs(before), "SIGTERM left a temp store behind"


def test_sigkill_leaves_an_orphan_that_the_next_run_sweeps() -> None:
    """SIGKILL cannot be trapped; the following run must reclaim the directory."""
    before = _detail_dirs()
    with tempfile.TemporaryDirectory() as holder:
        fixture = _fixture(Path(holder) / "data.csv", rows=40000)
        script = f"""
import sys, time
sys.path.insert(0, {str(REPO)!r})
from pathlib import Path
from app.config.settings import AnalysisConfig
from app.core import temp_store
from app.core.analysis_service import analyse_file
result = analyse_file(Path({str(fixture)!r}), AnalysisConfig(use_ai=False))
print(result.detail_store.temp_dir, flush=True)
time.sleep(5)
"""
        proc = subprocess.Popen([sys.executable, "-c", script],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        deadline = time.time() + 60
        store_dir = None
        while time.time() < deadline and store_dir is None:
            fresh = _new_detail_dirs(before)
            if fresh:
                store_dir = sorted(fresh)[-1]
            time.sleep(0.2)
        assert store_dir is not None
        proc.kill()
        proc.wait(timeout=30)
        assert Path(store_dir).exists(), "SIGKILL should leave the store behind"

        # Age it out, then let a fresh process sweep it.
        old = time.time() - 2 * 3600
        for item in Path(store_dir).rglob("*"):
            os.utime(item, (old, old))
        os.utime(store_dir, (old, old))

        temp_store._sweep_done = False
        report = temp_store.sweep_orphans()
        assert not Path(store_dir).exists(), "the orphan sweep did not reclaim the store"
        assert report["removed"] >= 1


def test_orphan_sweep_keeps_live_and_recent_stores() -> None:
    stale = BASE / f"{temp_store.PREFIX}stale-case"
    live_dir = BASE / f"{temp_store.PREFIX}live-case"
    recent = BASE / f"{temp_store.PREFIX}recent-case"
    stale.mkdir(exist_ok=True)
    live_dir.mkdir(exist_ok=True)
    recent.mkdir(exist_ok=True)

    from app.core.detail_store import DetailStore

    live = DetailStore(live_dir / "detail.sqlite", "live-case")
    live.temp_dir = live_dir
    temp_store.register_store(live)
    (recent / "detail.sqlite").write_bytes(b"\x00" * 2048)
    (stale / "detail.sqlite").write_bytes(b"\x00" * 2048)
    old = time.time() - 6 * 3600
    for item in (stale, stale / "detail.sqlite"):
        os.utime(item, (old, old))

    temp_store._sweep_done = False
    report = temp_store.sweep_orphans()

    assert not stale.exists(), "a stale orphan was not swept"
    assert live_dir.exists(), "the sweep deleted a store this process still owns"
    assert recent.exists(), "a store written minutes ago was swept too eagerly"
    assert report["removed"] >= 1

    # Explicit shutdown releases the live store, as the GUI/CLI shutdown does.
    temp_store.cleanup_all()
    assert not live_dir.exists()
    (recent / "detail.sqlite").unlink()
    recent.rmdir()


def test_sweep_never_touches_unrelated_temp_files() -> None:
    neighbour = BASE / "piiscan-not-a-detail-store.txt"
    neighbour.write_text("keep me")
    other = BASE / "someone-elses-temp-db.sqlite"
    other.write_bytes(b"y")
    temp_store._sweep_done = False
    temp_store.sweep_orphans()
    assert neighbour.exists() and other.exists()
    neighbour.unlink()
    other.unlink()




def test_sweep_skips_a_store_another_process_has_open() -> None:
    """A store owned by a *different* live process is never deleted."""
    import subprocess
    import sys

    holder = BASE / f"{temp_store.PREFIX}other-process"
    holder.mkdir(exist_ok=True)
    db = holder / "detail.sqlite"
    script = (
        "import sqlite3, time, sys\n"
        "conn = sqlite3.connect(sys.argv[1])\n"
        "conn.execute('CREATE TABLE t(x)')\n"
        "conn.commit()\n"
        "time.sleep(20)\n"
    )
    process = subprocess.Popen([sys.executable, "-c", script, str(db)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(200):
        if db.exists() and temp_store._open_by_other_process(holder):
            break
        time.sleep(0.05)
    else:
        process.kill()
        holder.rmdir()
        raise AssertionError("could not observe the other process holding the store open")

    temp_store._sweep_done = False
    temp_store.sweep_orphans(orphan_age_minutes=0)
    assert holder.exists(), "the sweep deleted a database another process still had open"

    process.wait()
    temp_store._sweep_done = False
    report = temp_store.sweep_orphans(orphan_age_minutes=0)
    assert not holder.exists(), "the store was not swept once its owner exited"
    assert report["removed"] >= 1
