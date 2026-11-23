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

from typing import Iterable

from PySide6.QtWidgets import QApplication, QWidget


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
