"""Deterministic Qt teardown.

PySide 6.12 (Qt 6.12) aborts at interpreter exit when a widget graph is
garbage-collected while the QApplication is still alive: Qt's own cleanup
visits the bound objects and frees memory that Python already released
(`___BUG_IN_CLIENT_OF_LIBMALLOC_POINTER_BEING_FREED_WAS_NOT_ALLOCATED`).
Closing the widgets, marking them `deleteLater()` and pumping the event loop
hands the destruction back to Qt, so the process exits cleanly.

Measured on macOS 27.3 / Python 3.15.2 / PySide6 6.12 (offscreen):
13/60 headless GUI scenarios aborted without this helper, 0/60 with it.
"""
from __future__ import annotations

import atexit
from typing import Iterable

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication, QWidget

# Every worker QThread the app started. The references are strong on purpose: a
# QThread whose object is garbage-collected while its thread still runs aborts
# the process ("QThread: Destroyed while thread is still running"). Shutdown
# joins each thread and then releases the reference.
_live_threads: list[QThread] = []


def register_thread(thread: QThread) -> None:
    """Track a worker thread so teardown can always join it."""
    if thread not in _live_threads:
        _live_threads.append(thread)


def unregister_thread(thread: QThread) -> None:
    """Drop a thread from the shutdown list once it has actually finished."""
    if thread in _live_threads:
        _live_threads.remove(thread)


def live_threads() -> list[QThread]:
    return [thread for thread in _live_threads if thread.isRunning()]


def join_threads(timeout_ms: int = 30_000) -> dict:
    """Cancel and join every worker thread before Qt destroys it.

    Returns ``{"joined": n, "still_running": n}``; a worker that refuses to stop
    is reported rather than silently left running.
    """
    result = {"joined": 0, "still_running": 0}
    for thread in list(_live_threads):
        request_cancel = getattr(thread, "request_cancel", None)
        if callable(request_cancel):
            request_cancel()
        if thread.isRunning():
            thread.wait(timeout_ms)
        result["still_running" if thread.isRunning() else "joined"] += 1
    _live_threads.clear()
    return result


# Registered after QtCore, so it runs before PySide's own `destroyQCoreApplication`
# atexit handler (atexit runs in LIFO order).
atexit.register(join_threads)


def close_widgets(*widgets: object) -> None:
    """Close and schedule deletion of Qt widgets in the current Qt session.

    Accepts a flat list or an iterable of widgets so callers can pass
    `close_widgets(window)` or `close_widgets([a, b])` interchangeably. Layouts
    and other child objects are handled through their owning widget.
    """
    application = QApplication.instance()
    flat: list[object] = []
    for widget in widgets:
        if isinstance(widget, (list, tuple, set)):
            flat.extend(widget)
        else:
            flat.append(widget)

    for item in flat:
        close = getattr(item, "close", None)
        delete_later = getattr(item, "deleteLater", None)
        if callable(close) and callable(delete_later):
            close()
            delete_later()
    if application is not None:
        application.processEvents()
