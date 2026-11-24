"""Lifecycle management for throwaway detail-store databases.

Every analysis that does not name a store creates a temporary directory
(prefixed ``piiscan-detail-``) holding a SQLite database plus its WAL/SHM
sidecars. If the process is killed, aborts, or simply exits without closing the
store, those gigabyte-sized directories stay in the system temp folder forever -
a few hundred test runs produced ~200 GB on the reference machine.

Three layers make that impossible:

1. ``register_store`` records every temporary store the process owns, and
   ``cleanup_all()`` (called at interpreter exit and on SIGINT/SIGTERM) closes
   the connection and removes the directory with its sidecars.
2. ``sweep_orphans()`` runs at process start and removes temporary stores left
   behind by *earlier* processes. A directory is only considered orphaned when
   it has not been touched for ``orphan_age_minutes`` and no live process has
   it open, so a parallel run's database is never deleted.
3. ``DetailStore.cleanup()`` is idempotent and error tolerant, so a duplicate
   call, or a store whose directory was already removed, is harmless.
"""
from __future__ import annotations

import atexit
import os
import signal
import tempfile
import time
from pathlib import Path
from threading import Lock

PREFIX = "piiscan-detail-"
OTHER_PREFIXES = ("piiscan-perf-",)  # synthetic benchmark fixtures
ORPHAN_AGE_MINUTES = 30

_live: list = []
_live_paths: set[str] = set()
_lock = Lock()
_handlers_installed = False
_sweep_done = False


def register_store(store) -> None:
    """Track a store that lives in a temporary directory so it is cleaned up."""
    if getattr(store, "temp_dir", None) is None:
        return
    with _lock:
        if store not in _live:
            _live.append(store)
            _live_paths.add(os.path.realpath(str(store.path)))
    install_signal_handlers()


def unregister_store(store) -> None:
    with _lock:
        if store in _live:
            _live.remove(store)
        _live_paths.discard(str(getattr(store, "path", "")))


def cleanup_all() -> None:
    """Close and delete every temporary store this process owns."""
    with _lock:
        stores = list(_live)
        _live.clear()
        _live_paths.clear()
    for store in stores:
        try:
            store.cleanup()
        except Exception:  # a half-deleted temp dir must not break shutdown
            pass


def live_paths() -> set[str]:
    with _lock:
        return set(_live_paths)


def _store_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _newest_mtime(path: Path) -> float:
    try:
        return max((f.stat().st_mtime for f in path.rglob("*") if f.is_file()), default=0.0)
    except OSError:
        return 0.0


def _in_use(path: Path, orphan_age_minutes: int) -> bool:
    """True when the store was written too recently to be considered abandoned."""
    newest = _newest_mtime(path)
    if not newest:
        return False
    return (time.time() - newest) < orphan_age_minutes * 60


def _open_by_other_process(path: Path) -> bool:
    """True when a live process still has a file inside the store open.

    Paths are compared after `realpath` resolution: on macOS the temp dir is
    reported as ``/var/...`` by tempfile but as ``/private/var/...`` by the
    kernel, so a raw string prefix match would miss every real holder.
    """
    try:
        import psutil
    except ImportError:
        return False
    prefixes = {os.path.realpath(str(path)), str(path)}
    me = os.getpid()
    for proc in psutil.process_iter(["pid"]):
        try:
            if proc.pid == me:
                continue
            for handle in proc.open_files():
                handle_path = os.path.realpath(handle.path)
                if any(handle_path.startswith(prefix) for prefix in prefixes):
                    return True
        except Exception:
            continue
    return False


def sweep_orphans(orphan_age_minutes: int = ORPHAN_AGE_MINUTES) -> dict:
    """Delete temporary stores left behind by earlier runs of this program.

    A candidate is removed only when it is not owned by this process, has not
    been written for ``orphan_age_minutes``, and no other process has it open,
    so a concurrently running analysis is never disrupted.
    """
    global _sweep_done
    if _sweep_done:
        return {"removed": 0, "bytes": 0, "skipped": 0}
    _sweep_done = True

    base = Path(tempfile.gettempdir())
    live = live_paths()
    removed = skipped = total = 0
    for prefix in (PREFIX, *OTHER_PREFIXES):
        for entry in base.glob(f"{prefix}*"):
            path = Path(entry)
            if path.is_dir() and any(os.path.realpath(str(path / name)) in live
                                    for name in ("detail.sqlite", "detail.db", "results.sqlite")):
                skipped += 1
                continue
            if path.is_dir() and _in_use(path, orphan_age_minutes):
                # Written recently: still open, or abandoned only minutes ago.
                # Leave it for the next sweep rather than risk deleting a live DB.
                skipped += 1
                continue
            if path.is_dir() and _open_by_other_process(path):
                skipped += 1
                continue
            try:
                total += _store_size(path)
                if path.is_dir():
                    for item in path.rglob("*"):
                        if item.is_file():
                            item.unlink()
                    path.rmdir()
                else:
                    path.unlink()
                removed += 1
            except OSError:
                skipped += 1
    return {"removed": removed, "bytes": total, "skipped": skipped, "base": str(base)}


def orphan_inventory() -> list[dict]:
    """Report temporary stores in the temp directory (for diagnostics)."""
    base = Path(tempfile.gettempdir())
    rows = []
    for prefix in (PREFIX, *OTHER_PREFIXES):
        for entry in sorted(base.glob(f"{prefix}*")):
            path = Path(entry)
            rows.append({
                "path": str(path),
                "bytes": _store_size(path),
                "in_use": str(path / "detail.sqlite") in live_paths()
                or _in_use(path, ORPHAN_AGE_MINUTES),
            })
    return rows


def install_signal_handlers() -> None:
    """Clean up temp stores on the normal termination paths.

    SIGINT keeps its KeyboardInterrupt semantics (Ctrl+C behaves as before) but
    the temporary databases are released first. SIGTERM and SIGBREAK exit after
    the cleanup. SIGKILL/SIGABRT cannot be trapped - those are what
    ``sweep_orphans()`` recovers from on the next run.
    """
    global _handlers_installed
    if _handlers_installed:
        return
    _handlers_installed = True

    def handle(signum, frame):  # noqa: N802 - signal signature
        cleanup_all()
        if signum == getattr(signal, "SIGINT", None):
            raise KeyboardInterrupt
        raise SystemExit(0)

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, handle)
        except (ValueError, OSError):
            pass  # not the main thread; the atexit handler still runs


atexit.register(cleanup_all)
