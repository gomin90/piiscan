"""Streaming CSV reader.

Rows are read lazily so a 130,000+ row file is never held in memory. Parse
errors are isolated per row and recorded separately from successfully parsed rows.
"""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field
from pathlib import Path

from app.core.csv_detector import FormatProfile, count_rows


@dataclass
class FileSettings:
    path: str
    encoding: str
    delimiter: str
    has_header: bool
    header_start_row: int = 0  # 0-based index of the header row (rows before data)
    table: str = ""
    schema: str = ""
    db_type: str = ""
    column_names: list[str] = field(default_factory=list)
    mode: str = "auto"

    @classmethod
    def from_profile(cls, profile: FormatProfile, **overrides) -> "FileSettings":
        stem = Path(profile.path).stem
        settings = cls(
            path=profile.path,
            encoding=profile.encoding,
            delimiter=profile.delimiter,
            has_header=profile.has_header,
            column_names=list(profile.column_names),
        )
        settings.table = overrides.get("table") or stem
        settings.schema = overrides.get("schema") or ""
        settings.db_type = overrides.get("db_type") or ""
        for key in ("header_start_row", "has_header", "encoding", "delimiter"):
            if overrides.get(key) is not None:
                setattr(settings, key, overrides[key])
        return settings


class CsvLoader:
    """Yield normalised rows while tracking parse problems and row counts."""

    def __init__(self, settings: FileSettings):
        self.settings = settings
        self.parsed_rows = 0
        self.error_rows = 0
        self.empty_rows = 0
        self.irregular_rows = 0
        self.column_count = 0
        self.column_names: list[str] = []
        self.total_rows: int | None = None
        self.row_errors: list[str] = []

    def _column_names(self) -> list[str]:
        """Resolve column names from the header, or generate COL_001 style names."""
        with open(self.settings.path, "r", encoding=self.settings.encoding, errors="replace", newline="") as handle:
            rows = []
            for line in handle:
                parsed = next(csv.reader([line], delimiter=self.settings.delimiter))
                if parsed and any(cell.strip() for cell in parsed):
                    rows.append(parsed)
                if len(rows) >= 5:
                    break
        if not rows:
            return []
        width = max(len(row) for row in rows)
        if self.settings.has_header:
            index = self.settings.header_start_row if self.settings.header_start_row < len(rows) else 0
            header = rows[index]
        else:
            header = [""] * width
        names = []
        for i, name in enumerate(header):
            name = (name or "").strip()
            if not name or not name.replace("_", "").isalnum():
                name = f"COL_{i + 1:03d}"
            names.append(name)
        for i in range(len(names), width):
            names.append(f"COL_{i + 1:03d}")
        return names

    def prepare(self) -> None:
        if not self.column_names:
            self.column_names = self._column_names()
            self.column_count = len(self.column_names)
        if self.total_rows is None:
            try:
                # One cheap pre-pass gives an accurate progress denominator
                # without ever holding the file in memory.
                self.total_rows = count_rows(self.settings.path, self.settings.encoding, self.settings.delimiter)
                self.total_rows = max(0, self.total_rows - self.rows_to_skip())
            except OSError:
                self.total_rows = None

    def rows_to_skip(self) -> int:
        if self.settings.has_header:
            return self.settings.header_start_row + 1
        return self.settings.header_start_row

    def iter_rows(self):
        """Yield (row_index, row) with rows padded/truncated to the column count."""
        with open(self.settings.path, "r", encoding=self.settings.encoding, errors="replace", newline="") as handle:
            for _ in range(self.rows_to_skip()):
                try:
                    next(handle)
                except StopIteration:
                    break
            reader = csv.reader(handle, delimiter=self.settings.delimiter)
            index = 0
            for raw in reader:
                if not raw or all(not cell.strip() for cell in raw):
                    self.empty_rows += 1
                    continue
                try:
                    width = len(raw)
                    if width != self.column_count:
                        self.irregular_rows += 1
                        if width < self.column_count:
                            raw = raw + [""] * (self.column_count - width)
                        else:
                            raw = raw[: self.column_count]
                    self.parsed_rows += 1
                    yield index, raw
                    index += 1
                except Exception as exc:  # isolate the row, keep analysing
                    self.error_rows += 1
                    if len(self.row_errors) < 50:
                        self.row_errors.append(f"행 {index}: {exc}")

    def progress_total(self) -> int:
        return self.total_rows or (self.parsed_rows + self.error_rows + self.empty_rows)


def file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0
