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


# A pathological field cannot consume unbounded RAM.
FIELD_LIMIT_CEILING = 100_000_000


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

    def __init__(self, settings: FileSettings, max_field_chars: int = 10_000_000):
        self.settings = settings
        self.max_field_chars = max(1, max_field_chars)
        self.parsed_rows = 0
        self.error_rows = 0
        self.empty_rows = 0
        self.irregular_rows = 0
        self.short_rows = 0
        self.extra_rows = 0
        self.max_extra_fields = 0
        self.oversized_fields = 0
        self.field_limit = 0
        self.unparsed_rows = 0
        self.column_count = 0
        self.header_length = 0
        self.column_names: list[str] = []
        self.total_rows: int | None = None
        self.row_errors: list[str] = []

    def _reader(self, handle):
        # The default csv field limit is 131072 characters, which aborts on big
        # JSON/Base64 columns. We state the limit explicitly and record it, so a
        # field that exceeds it is counted as an error rather than lost silently.
        self.field_limit = min(FIELD_LIMIT_CEILING, max(1, self.max_field_chars))
        csv.field_size_limit(self.field_limit)
        return csv.reader(handle, delimiter=self.settings.delimiter)

    def _column_names(self) -> list[str]:
        """Resolve column names from the header, or generate COL_001 style names."""
        with open(self.settings.path, "r", encoding=self.settings.encoding, errors="replace", newline="") as handle:
            rows = []
            try:
                for parsed in self._reader(handle):
                    if parsed and any(cell.strip() for cell in parsed):
                        rows.append(parsed)
                    if len(rows) >= 5:
                        break
            except csv.Error as exc:
                self.row_errors.append(f"헤더 읽기 실패: {exc}")
        if not rows:
            return []
        width = max(len(row) for row in rows)
        if self.settings.has_header:
            index = self.settings.header_start_row if self.settings.header_start_row < len(rows) else 0
            header = rows[index]
        else:
            header = [""] * width
        # The declared header width is the reference for "extra" fields: every
        # row that carries more fields than the header has EXTRA_COL cells.
        self.header_length = len(header) if self.settings.has_header else width
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
        if not self.header_length:
            self.header_length = self.column_count
        if self.total_rows is None:
            try:
                # One cheap pre-pass gives an accurate progress denominator
                # without ever holding the file in memory.
                self.total_rows = count_rows(
                    self.settings.path, self.settings.encoding, self.settings.delimiter,
                    min(FIELD_LIMIT_CEILING, max(1, self.max_field_chars)),
                )
                self.total_rows = max(0, self.total_rows - self.rows_to_skip())
            except OSError:
                self.total_rows = None

    def rows_to_skip(self) -> int:
        if self.settings.has_header:
            return self.settings.header_start_row + 1
        return self.settings.header_start_row

    def iter_rows(self):
        """Yield (row_index, row).

        Short rows are padded to the header width. Rows with *more* fields than the
        header are yielded intact: truncating them would discard cells that may hold
        personal data, so the extra fields are analysed as EXTRA_COL_n columns and the
        row is reported as irregular.

        A row that the parser cannot read at all (an oversized field, an unbalanced
        quote) is counted in `error_rows` / `unparsed_rows` and never silently
        disappears from the report.
        """
        with open(self.settings.path, "r", encoding=self.settings.encoding, errors="replace", newline="") as handle:
            reader = self._reader(handle)
            for _ in range(self.rows_to_skip()):
                try:
                    next(reader)
                except StopIteration:
                    break
                except csv.Error as exc:
                    self.error_rows += 1
                    self.unparsed_rows += 1
                    if len(self.row_errors) < 50:
                        self.row_errors.append(f"헤더 Row parse error: {exc}")
            index = 0
            while True:
                try:
                    raw = next(reader)
                except StopIteration:
                    break
                except csv.Error as exc:
                    # csv.Error is raised by the iterator itself, so it has to be
                    # caught here or the whole file analysis aborts.
                    self.error_rows += 1
                    self.unparsed_rows += 1
                    message = str(exc)
                    if "field larger than field limit" in message:
                        self.oversized_fields += 1
                    if len(self.row_errors) < 50:
                        self.row_errors.append(f"행 {index}: {message}")
                    continue
                if not raw or all(not cell.strip() for cell in raw):
                    self.empty_rows += 1
                    continue
                width = len(raw)
                reference = self.header_length or self.column_count
                if width < reference:
                    self.short_rows += 1
                    raw = raw + [""] * (reference - width)
                elif width > reference:
                    self.extra_rows += 1
                    self.max_extra_fields = max(self.max_extra_fields, width - reference)
                if width != reference:
                    self.irregular_rows += 1
                self.parsed_rows += 1
                yield index, raw
                index += 1

    def progress_total(self) -> int:
        return self.total_rows or (self.parsed_rows + self.error_rows + self.empty_rows)


def file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0
