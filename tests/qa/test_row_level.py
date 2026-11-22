"""Row-level (cell-by-cell) PII detection QA.

The product requirement is that **every cell of every row** is inspected and that
a hit is reported at the row/cell that produced it, regardless of how the column
as a whole is classified. These tests measure detection at the cell and row
level, including the cases where column names are meaningless or only a handful
of rows in a column contain personal data.
"""
from __future__ import annotations

import csv
import tempfile
import time
from pathlib import Path

from app.config.settings import AnalysisConfig
from app.core.analysis_service import analyse_file
from app.export.hit_exporter import collect_hit_cells, collect_row_records
from tests.qa.dataset import DATASET, bank_account, imei, resident

RESIDENT = resident("900101", "123456")
RESIDENT2 = resident("850312", "987654")
ACCOUNT = bank_account("123456789012")
IMEI_VALUE = imei("86972001234567")


def _write(rows: list[list[str]], header: list[str] | None = None) -> Path:
    directory = Path(tempfile.mkdtemp())
    path = directory / "rows.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        if header:
            writer.writerow(header)
        writer.writerows(rows)
    return path


def _analyse(rows: list[list[str]], header: list[str] | None = None, **overrides) -> object:
    config = AnalysisConfig(use_ai=False)
    for key, value in overrides.items():
        setattr(config, key, value)
    return analyse_file(_write(rows, header), config)


# --------------------------------------------------------------------------
# 1. A row with no personal data produces no row record.
# --------------------------------------------------------------------------
def test_clean_row_is_not_reported() -> None:
    result = _analyse(
        [["1000", "2024-01-05T10:00:00", "42", "normal text"]],
        ["AMOUNT", "UPDATED_AT", "QTY", "MEMO"],
    )
    assert result.row_records == []
    counts = result.cell_counts
    assert counts["rows_inspected"] == 1
    assert counts["cells_inspected"] == 4
    assert counts["pii_cells"] == 0


# --------------------------------------------------------------------------
# 2. Exactly one personal-data cell in a row is reported with its position.
# --------------------------------------------------------------------------
def test_single_cell_hit_is_located() -> None:
    result = _analyse(
        [["1000", RESIDENT, "note only"],
         ["2000", "100", "note only"]],
        ["FIELD_001", "COL_01", "MEMO"],
    )
    records = collect_row_records([result])
    assert len(records) == 1
    record = records[0]
    assert record["row_number"] == 1
    assert record["has_pii"] == "예"
    assert record["columns"] == "COL_01"
    assert record["column_indexes"] == "1"
    assert "RESIDENT_ID" in record["pii_types"]

    cells = collect_hit_cells([result])
    assert len(cells) == 1
    assert cells[0]["masked_value"] != RESIDENT
    assert cells[0]["privacy_type"] == "RESIDENT_ID"
    assert cells[0]["verdict"] == "개인정보"


# --------------------------------------------------------------------------
# 3. Several different types in the same row are each reported separately.
# --------------------------------------------------------------------------
def test_multiple_types_in_one_row() -> None:
    result = _analyse(
        [["김민준", "19850312", "01012345678", "min.jun@example.com", "1200", "1234-567890123"]],
        ["NAME", "BIRTH", "PHONE", "EMAIL", "AMOUNT", "REF"],
    )
    records = collect_row_records([result])
    assert len(records) == 1
    types = set(records[0]["pii_types"].split(", "))
    assert {"NAME", "BIRTH_DATE", "PHONE_NUMBER", "EMAIL", "BANK_ACCOUNT"} <= types
    assert records[0]["risk_level"] in ("높음", "매우 높음")
    assert "Row 조합" in records[0]["basis"]
    # Each detected cell is its own line with its own column index.
    cells = [cell for cell in collect_hit_cells([result]) if cell["row_number"] == 1]
    columns = {cell["column"] for cell in cells}
    assert {"NAME", "BIRTH", "PHONE", "EMAIL", "REF"} <= columns
    assert len(cells) == len({(c["row_number"], c["column_index"]) for c in cells})


# --------------------------------------------------------------------------
# 4. Meaningless column names do not prevent detection.
# --------------------------------------------------------------------------
def test_meaningless_column_names_still_detect() -> None:
    result = _analyse(
        [[RESIDENT, "E12345678", "20151234-56", "min.jun@example.com", IMEI_VALUE],
         [RESIDENT2, "123", "789", "hong@example.com", "42"]],
        ["FIELD_001", "COL_02", "ATTR_3", "X", "VAL"],
    )
    records = collect_row_records([result])
    assert len(records) == 2
    first = set(records[0]["pii_types"].split(", "))
    assert {"RESIDENT_ID", "PASSPORT", "DRIVER_LICENSE", "EMAIL", "IMEI"} <= first
    second = set(records[1]["pii_types"].split(", "))
    assert {"RESIDENT_ID", "EMAIL"} <= second
    for record in records:
        assert record["column_indexes"] != ""


# --------------------------------------------------------------------------
# 5. Only some rows of a column contain personal data: those rows are reported.
# --------------------------------------------------------------------------
def test_partial_column_hits_are_reported() -> None:
    rows = [[str(index * 13), f"item-{index}"] for index in range(1, 1000)]
    rows[42] = [RESIDENT, "item-43"]          # resident number in an ordinary column
    rows[777] = ["01012345678", "item-778"]   # phone number in the same column
    result = _analyse(rows, ["VALUE", "ITEM"])

    records = collect_row_records([result])
    detected_rows = {record["row_number"] for record in records}
    assert 43 in detected_rows and 778 in detected_rows
    for record in records:
        if record["row_number"] == 43:
            assert "RESIDENT_ID" in record["pii_types"]
            assert record["has_pii"] == "예"
        if record["row_number"] == 778:
            assert "PHONE_NUMBER" in record["pii_types"]
    # Column-level classification stays conservative, but it cannot hide the cells.
    value_findings = [f for f in result.findings if f["column"] == "VALUE"]
    assert all(not t["decision"] == "개인정보"
               for f in value_findings for t in f["types"])


# --------------------------------------------------------------------------
# 6. Wide CSV (10,000 columns) is inspected cell by cell.
# --------------------------------------------------------------------------
def test_wide_csv_is_fully_scanned() -> None:
    width = 10_000
    header = [f"C_{i:05d}" for i in range(width)]
    rows = [["x" for _ in range(width)] for _ in range(5)]
    rows[2][3999] = RESIDENT
    rows[4][9999] = "min.jun@example.com"
    result = _analyse(rows, header)

    counts = result.cell_counts
    assert counts["rows_inspected"] == 5
    assert counts["cells_inspected"] == 5 * width
    records = collect_row_records([result])
    assert {record["row_number"] for record in records} == {3, 5}
    assert {record["column_indexes"] for record in records} == {"3999", "9999"}


# --------------------------------------------------------------------------
# 7. Large row counts are streamed and counted exactly.
# --------------------------------------------------------------------------
def test_bulk_rows_are_counted() -> None:
    rows = [[str(i), f"user{i}@example.com", f"backup{i}@example.com"] for i in range(1, 50_001)]
    started = time.perf_counter()
    result = _analyse(rows, ["ID", "MAIL", "BACKUP"])
    elapsed = time.perf_counter() - started

    counts = result.cell_counts
    assert counts["rows_inspected"] == 50_000
    assert counts["cells_inspected"] == 150_000
    assert counts["rows_with_hits"] == 50_000
    assert counts["pii_cells"] >= 100_000
    assert counts["detail_truncated_rows"] == 45_000  # detail rows capped at max_detail_rows
    assert counts["detail_limit"] == 5000
    assert counts["detail_truncated_rows"] == 45_000
    assert len(result.row_records) == 5000
    assert elapsed < 60.0

    records = collect_row_records([result])
    assert len(records) == 5000


# --------------------------------------------------------------------------
# 8. Headerless CSVs are detected from the values alone.
# --------------------------------------------------------------------------
def test_headerless_csv_detection() -> None:
    result = _analyse(
        [[RESIDENT, "김민준", "01012345678", "min.jun@example.com", "서울시 강남구 테헤란로 152"],
         [RESIDENT2, "홍길동", "01098765432", "hong@example.com", "부산시 해운대구 123"],
         ["123456", "plain text", "99", "0", "reference-1"]],
    )
    records = collect_row_records([result])
    assert len(records) == 2  # the third row has no personal data
    first = records[0]
    assert "RESIDENT_ID" in first["pii_types"]
    assert "EMAIL" in first["pii_types"]
    assert "NAME" in first["pii_types"]
    assert first["column_indexes"] != ""


# --------------------------------------------------------------------------
# Cell-level and row-level precision / recall / F1
# --------------------------------------------------------------------------
def _ratios(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def _measure(natural_names: bool) -> dict:
    from tests.qa.metrics import cell_row_metrics
    return cell_row_metrics(natural_names)


def test_cell_and_row_level_metrics_with_column_context() -> None:
    metrics = _measure(natural_names=True)
    assert metrics["cell"]["precision"] >= 0.90, metrics["cell"]
    assert metrics["cell"]["recall"] >= 0.90, metrics["cell"]
    assert metrics["cell"]["f1"] >= 0.90
    assert metrics["row"]["precision"] >= 0.95, metrics["row"]
    assert metrics["row"]["recall"] >= 0.90, metrics["row"]
    assert metrics["row"]["f1"] >= 0.90


def test_cell_and_row_level_metrics_without_column_context() -> None:
    """Meaningless column names: values must still be judged from the data alone."""
    metrics = _measure(natural_names=False)
    assert metrics["cell"]["precision"] >= 0.90, metrics["cell"]
    assert metrics["cell"]["recall"] >= 0.80, metrics["cell"]
    assert metrics["row"]["precision"] >= 0.90, metrics["row"]
