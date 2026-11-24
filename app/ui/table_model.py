"""SQLite-backed table models for the result views.

The widgets never hold the whole result set: a `QTableView` reads one page at a
time through these models, so a file with tens of millions of cells renders a
page of a few hundred rows and the memory stays bounded by the page size.
"""
from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, Qt


class PagedTableModel(QAbstractTableModel):
    """A read-only model over a page of records fetched from the detail store."""

    def __init__(self, headers: list[str], records: list[dict] | None = None) -> None:
        super().__init__()
        self.headers = headers
        self.records = records or []

    def set_records(self, records: list[dict]) -> None:
        self.beginResetModel()
        self.records = records
        self.endResetModel()

    def rowAt(self, index: int) -> dict:  # noqa: N802 - Qt naming
        return self.records[index]

    def rowCount(self, parent=None) -> int:  # noqa: N802 - Qt naming
        return len(self.records)

    def columnCount(self, parent=None) -> int:  # noqa: N802 - Qt naming
        return len(self.headers)

    def data(self, index, role=Qt.DisplayRole):  # noqa: N802 - Qt naming
        if role != Qt.DisplayRole or not index.isValid():
            return None
        value = self.records[index.row()].get(self.headers[index.column()], "")
        if isinstance(value, (list, tuple)):
            return ", ".join(str(item) for item in value)
        if isinstance(value, bool):
            return "예" if value else "아니요"
        return str(value)

    def headerData(self, section, orientation, role=Qt.DisplayRole):  # noqa: N802 - Qt naming
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.headers[section]
        return None
