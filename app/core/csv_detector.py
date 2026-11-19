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


def _split_lines(text: str) -> list[str]:
    return [line for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]


def detect_delimiter(lines: list[str]) -> str:
    """Choose the delimiter that produces the most consistent column count."""
    scored: dict[str, float] = {}
    candidates = list(SUPPORTED_DELIMITERS)
    for delim in candidates:
        counts = []
        for line in lines[:40]:
            if not line.strip():
                continue
            counts.append(line.count(delim))
        if not counts:
            continue
        nonzero = [c for c in counts if c > 0]
        if not nonzero:
            continue
        mode = max(set(nonzero), key=nonzero.count)
        consistency = nonzero.count(mode) / len(nonzero)
        scored[delim] = consistency * (mode + 1)
    if not scored:
        return ","
    best = max(scored.items(), key=lambda item: item[1])
    # A comma inside quoted text still leaves tabs/pipes as the real delimiter.
    if best[0] == "," and any('"' in line for line in lines[:20]):
        for delim in ("\t", "|", ";"):
            if any(delim in line for line in lines[:20]) and scored.get(delim, 0) >= best[1] * 0.5:
                return delim
    return best[0]


def _looks_like_header(lines: list[str], delimiter: str) -> bool:
    if len(lines) < 2:
        return True
    first = [c.strip() for c in next(csv.reader([lines[0]], delimiter=delimiter))]
    if not first or any(not c for c in first):
        return False
    second = [c.strip() for c in next(csv.reader([lines[1]], delimiter=delimiter))]
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

    lines = _split_lines(text)
    content_lines = [line for line in lines if line.strip()]
    if not content_lines:
        return FormatProfile(str(p), encoding, ",", False, 0, [], 0.0, ["빈 파일"], binary_like=True)

    delimiter = detect_delimiter(content_lines)
    rows = list(csv.reader(content_lines[:200], delimiter=delimiter))
    if not rows:
        return FormatProfile(str(p), encoding, delimiter, False, 0, [], 0.0, ["행 parsing 실패"], binary_like=True)

    has_header = _looks_like_header(content_lines, delimiter)
    header = rows[0] if has_header else rows[0]
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
