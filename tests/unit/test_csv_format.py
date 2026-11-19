"""CSV 형식 탐지 및 스트리밍 loading tests."""
from __future__ import annotations

from pathlib import Path

from app.core.csv_detector import detect_delimiter, detect_format
from app.core.csv_loader import CsvLoader, FileSettings
from tests.fixtures.generate_fixtures import FIXTURE_DIR, generate


def test_encoding_and_delimiter_detection():
    generate()
    cases = {
        "utf8_comma.csv": (",", True),
        "utf8_bom.csv": (",", True),
        "cp949.csv": (",", True),
        "euckr.csv": (",", True),
        "utf16.csv": (",", True),
        "semicolon.csv": (";", True),
        "tab.csv": ("\t", True),
        "pipe.csv": ("|", True),
        "no_header.csv": (",", False),
    }
    for name, (delimiter, header) in cases.items():
        profile = detect_format(FIXTURE_DIR / name)
        assert profile.delimiter == delimiter, (name, profile.delimiter)
        assert profile.has_header is header, (name, profile.has_header)
        assert profile.column_count >= 3


def test_headerless_columns_get_placeholders():
    profile = detect_format(FIXTURE_DIR / "no_header.csv")
    assert profile.column_names[0] == "COL_001"
    assert profile.column_names[-1].startswith("COL_")


def test_rows_are_counted_and_isolated():
    settings = FileSettings(
        path=str(FIXTURE_DIR / "irregular.csv"),
        encoding="utf-8",
        delimiter=",",
        has_header=True,
    )
    loader = CsvLoader(settings)
    loader.prepare()
    rows = list(loader.iter_rows())
    assert loader.parsed_rows == len(rows)
    assert loader.irregular_rows > 0
    assert loader.parsed_rows >= 8


def test_binary_content_is_not_treated_as_csv(tmp_path: Path):
    bogus = tmp_path / "not_csv.bin"
    bogus.write_bytes(b"\x00\x01\x02\x03\x04\x05\x06\x07")
    profile = detect_format(bogus)
    assert profile.binary_like


def test_quoted_newline_rows_are_parsed():
    settings = FileSettings(str(FIXTURE_DIR / "quoted_newline.csv"), "utf-8", ",", True)
    loader = CsvLoader(settings)
    loader.prepare()
    rows = list(loader.iter_rows())
    assert any("\n" in "".join(row) for _, row in rows)
