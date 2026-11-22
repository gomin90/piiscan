"""Automatic CSV format detection: encoding, delimiter and header presence.

Detection is deliberately conservative: unsupported fixed-width or binary files
are reported as unparsable rather than silently treated as CSV.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.config.settings import SUPPORTED_DELIMITERS, SUPPORTED_ENCODINGS

ENCODING_CANDIDATES = [
    "utf-8-sig",
    "utf-8",
    "cp949",
    "euc-kr",
    "utf-16",
    "utf-16-le",
    "utf-16-be",
    "latin-1",
]

# Extensions that are never treated as CSV.
NON_CSV_SUFFIXES = {".xls", ".xlsx", ".bin", ".dat", ".parquet", ".db", ".zip", ".jpg", ".png", ".pdf"}

BINARY_HINT_RE = re.compile(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]{4,}")


@dataclass
class FormatProfile:
    path: str
    encoding: str
    delimiter: str
    has_header: bool
    column_count: int
    column_names: list[str]
    confidence: float
    notes: list[str] = field(default_factory=list)
    preview_rows: list[list[str]] = field(default_factory=list)
    total_rows: int | None = None
    binary_like: bool = False

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "encoding": self.encoding,
            "delimiter": "\t" if self.delimiter == "\t" else self.delimiter,
            "has_header": self.has_header,
            "column_count": self.column_count,
            "column_names": self.column_names,
            "confidence": round(self.confidence, 3),
            "notes": self.notes,
        }


def _sniff_encoding(raw: bytes) -> tuple[str, str]:
    """Return (encoding, decoded_text) using byte-order marks and byte heuristics.

    The prefix is always cut at a line boundary so a multi-byte character is
    never split in half (which would make UTF-8 files look like UTF-16).
    """
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig", raw.decode("utf-8-sig", errors="replace")
    if raw.startswith(b"\xff\xfe"):
        return "utf-16", raw.decode("utf-16", errors="replace")
    if raw.startswith(b"\xfe\xff"):
        return "utf-16-be", raw.decode("utf-16-be", errors="replace")

    cut = raw.rfind(b"\n")
    if cut > 0:
        head = raw[:cut]
    else:
        head = raw
    if b"\x00" in head:
        return "utf-16", head.decode("utf-16", errors="replace")
    for encoding in ENCODING_CANDIDATES:
        try:
            decoded = head.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        return "utf-8" if encoding == "utf-8-sig" else encoding, decoded
    return "latin-1", head.decode("latin-1", errors="replace")


def logical_rows(text: str, delimiter: str, limit: int = 400) -> list[list[str]]:
    """Parse *logical* CSV records, honouring quoted fields that span lines.

    A physical line is not a record: `a,"b\nc",d` is one record on three lines.
    Everything downstream (delimiter choice, header detection, column count) uses
    these records so a quoted comma, tab, semicolon or newline cannot split a row.
    """
    rows: list[list[str]] = []
    for row in csv.reader(io.StringIO(text, newline=""), delimiter=delimiter):
        if row:
            rows.append(row)
        if len(rows) >= limit:
            break
    return rows


def detect_delimiter(text: str) -> str:
    """Choose the delimiter that produces the most consistent record structure."""
    scored: dict[str, float] = {}
    for delim in SUPPORTED_DELIMITERS:
        rows = logical_rows(text, delim, limit=100)
        if len(rows) < 2:
            continue
        widths = [len(row) for row in rows]
        if max(widths) <= 1:
            continue
        mode = max(set(widths), key=widths.count)
        consistency = widths.count(mode) / len(widths)
        if mode <= 1 or consistency < 0.3:
            continue
        # Prefer the delimiter that keeps quoted content intact: a wrong
        # delimiter breaks quoted fields into extra columns.
        quoted_rows = sum(1 for row in rows if any('"' in cell for cell in row))
        scored[delim] = consistency * mode + (0.5 if quoted_rows else 0.0)
    if not scored:
        return ","
    return max(scored.items(), key=lambda item: item[1])[0]


def _looks_like_header(rows: list[list[str]], delimiter: str) -> bool:
    if len(rows) < 2:
        return True
    first = [c.strip() for c in rows[0]]
    if not first or any(not c for c in first):
        return False
    second = [c.strip() for c in rows[1]]
    if len(first) != len(second):
        return True
    # Header rows are short, letter-heavy and rarely numeric.
    numeric_first = sum(1 for c in first if re.fullmatch(r"\d+|\d{1,3}[-.]\d+", c))
    numeric_second = sum(1 for c in second if re.fullmatch(r"\d+|\d{1,3}[-.]\d+", c))
    if numeric_first == 0 and numeric_second > 0:
        return True
    if numeric_first >= len(first) * 0.7 and numeric_second >= len(second) * 0.7:
        return False
    # Generic placeholders (COL_1, C001) count as a header row.
    if all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", c) for c in first):
        return True
    return numeric_first < numeric_second


def detect_format(path: str | Path, preview_lines: int = 20) -> FormatProfile:
    p = Path(path)
    # Only the head of the file is read for format detection (bounded memory).
    with open(p, "rb") as handle:
        raw = handle.read(262144)
    if p.suffix.lower() in NON_CSV_SUFFIXES:
        return FormatProfile(str(p), "unknown", ",", False, 0, [], 0.0, ["지원하지 않는 파일 형식"], binary_like=True)

    encoding, text = _sniff_encoding(raw[:65536])
    if BINARY_HINT_RE.search(raw[:4096]) and text.count("\n") < 2:
        return FormatProfile(str(p), encoding, ",", False, 0, [], 0.0, ["바이너리 파일로 판단됨"], binary_like=True)

    if not text.strip():
        return FormatProfile(str(p), encoding, ",", False, 0, [], 0.0, ["빈 파일"], binary_like=True)

    delimiter = detect_delimiter(text)
    rows = logical_rows(text, delimiter, limit=200)
    if not rows:
        return FormatProfile(str(p), encoding, delimiter, False, 0, [], 0.0, ["행 parsing 실패"], binary_like=True)

    has_header = _looks_like_header(rows, delimiter)
    header = rows[0]
    notes: list[str] = []
    if not has_header:
        notes.append("헤더가 없는 파일로 판단됨")

    confidence = 0.5
    widths = {len(r) for r in rows if r}
    if len(widths) == 1:
        confidence += 0.3
    if has_header:
        confidence += 0.2

    column_names = header
    if not has_header:
        column_count = max(widths)
        column_names = [f"COL_{i + 1:03d}" for i in range(column_count)]

    return FormatProfile(
        path=str(p),
        encoding=encoding,
        delimiter=delimiter,
        has_header=has_header,
        column_count=len(column_names),
        column_names=column_names,
        confidence=min(confidence, 1.0),
        notes=notes,
        preview_rows=rows[:preview_lines],
    )


def read_preview(path: str | Path, encoding: str, delimiter: str, rows: int = 20) -> list[list[str]]:
    out: list[list[str]] = []
    with open(path, "r", encoding=encoding, errors="replace", newline="") as handle:
        for row in csv.reader(handle, delimiter=delimiter):
            out.append(row)
            if len(out) >= rows:
                break
    return out


def count_rows(path: str | Path, encoding: str, delimiter: str) -> int:
    """Count data rows without loading the whole file."""
    total = 0
    with open(path, "r", encoding=encoding, errors="replace", newline="") as handle:
        for row in csv.reader(handle, delimiter=delimiter):
            if row:
                total += 1
    return total
