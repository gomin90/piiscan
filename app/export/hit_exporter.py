"""Row- and cell-level detection detail export.

Two views of the same evidence, both streamed from the SQLite detail store so a
file with more hits than the memory cache still exports completely:

* `privacy_hits_*.csv` - one line per detected **cell**: source row, column name,
  column index, privacy type, masked value, the cell verdict and the rule evidence
  behind it, plus the row context that produced the row-level verdict.
* `privacy_rows_*.csv` - one line per **row** that produced at least one cell hit:
  whether the record holds *confirmed* personal data or only data that needs
  review, which types, in which columns, and why.

Raw personal values never reach these files: every value stored in the engine is
already the output of `mask_value()`. Values that could be read as a spreadsheet
formula (`=`, `+`, `-`, `@`) are escaped so Excel cannot execute them.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

from app.utils.csv_safety import formula_safe

# (record key, CSV column). `_write` emits exactly these columns in this order,
# so a record can never drift out of alignment with its header row.
CELL_FIELDS = [
    ("analysed_at", "분석일시"), ("file", "파일명"), ("db_type", "원본DB"),
    ("schema", "스키마"), ("table", "테이블명"), ("row_number", "행번호"),
    ("column", "컬럼명"), ("column_index", "컬럼 인덱스"),
    ("privacy_type", "개인정보유형"), ("verdict", "판정"),
    ("masked_value", "탐지값(마스킹)"), ("evidence", "판단근거"),
    ("confidence", "Cell 신뢰도"), ("needs_review", "검토 필요"),
    ("row_verdict", "Row 판정"), ("row_types", "Row 확정 유형"),
    ("row_review_types", "Row 검토 유형"), ("row_basis", "Row 근거"),
    ("row_risk", "Row 위험도"), ("ai_row", "AI Row 판정"),
]

ROW_FIELDS = [
    ("analysed_at", "분석일시"), ("file", "파일명"), ("db_type", "원본DB"),
    ("schema", "스키마"), ("table", "테이블명"), ("row_number", "행번호"),
    ("status", "개인정보 상태"), ("confirmed_pii", "확정 개인정보"),
    ("possible_pii", "검토 필요 개인정보"), ("has_pii", "개인정보 존재"),
    ("columns", "발생 컬럼"), ("column_indexes", "컬럼 인덱스"),
    ("cell_count", "탐지 Cell 수"), ("basis", "판정 근거"),
    ("confidence", "Row 신뢰도"), ("risk_level", "Row 위험도"),
    ("needs_review", "검토 필요"), ("ai_row", "AI Row 판정"),
]

CELL_HEADERS = [header for _, header in CELL_FIELDS]
ROW_HEADERS = [header for _, header in ROW_FIELDS]

# Re-exported for the QA tests.
__all__ = ["CELL_HEADERS", "ROW_HEADERS", "CELL_FIELDS", "ROW_FIELDS",
           "formula_safe", "iter_hit_cells",
           "iter_row_records", "iter_row_export_records", "collect_hit_cells",
           "collect_row_records", "write_hit_rows", "write_row_records",
           "collect_hit_rows"]


def _file_meta(result) -> tuple[str, str, str, str, str]:
    settings = result.settings if isinstance(result.settings, dict) else {}
    return (
        settings.get("analysed_at", ""),
        formula_safe(Path(result.path).name),
        formula_safe(settings.get("db_type", "")),
        formula_safe(settings.get("schema", "")),
        formula_safe(settings.get("table", Path(result.path).stem)),
    )


def iter_row_records(file_results: Iterable) -> Iterable[dict]:
    """Stream every row verdict from the detail store (no memory cap)."""
    for result in file_results:
        store = getattr(result, "detail_store", None)
        if store is not None:
            yield from store.iter_rows()
        else:
            for record in getattr(result, "row_records", None) or []:
                yield record.as_dict()


def _fallback_cells(result) -> Iterable[dict]:
    """Cell rows for results with no detail store (the in-memory test path)."""
    for record in getattr(result, "row_records", None) or []:
        for cell in record.cells:
            yield {
                **cell.as_dict(),
                "row_status": record.status,
                "row_confirmed": record.confirmed_pii,
                "row_possible": record.possible_pii,
                "row_basis": record.basis,
                "row_risk": record.risk_level,
                "row_ai": record.ai_review,
            }


def iter_hit_cells(file_results: Iterable) -> Iterable[dict]:
    """Stream one formatted record per detected cell, row by row.

    SQLite pages the records, so a 2,000,000-cell export never needs a
    2,000,000-element Python list.
    """
    for result in file_results:
        analysed_at, file_name, db_type, schema, table = _file_meta(result)
        store = getattr(result, "detail_store", None)
        cells = store.iter_cells_with_row_context() if store is not None else _fallback_cells(result)
        for cell in cells:
            yield {
                "analysed_at": analysed_at,
                "file": file_name,
                "db_type": db_type,
                "schema": schema,
                "table": table,
                "row_number": cell["row"],
                "column": formula_safe(cell["column"]),
                "column_index": cell["column_index"],
                "privacy_type": formula_safe(cell["type_key"]),
                "verdict": formula_safe(cell.get("decision") or "개인정보"),
                "masked_value": formula_safe(cell["masked_value"]),
                "evidence": formula_safe(cell["evidence"]),
                "confidence": round(cell["confidence"], 3),
                "needs_review": "예" if cell["needs_review"] else "아니요",
                "row_verdict": formula_safe(cell.get("row_status", "검토 필요")),
                "row_types": ", ".join(formula_safe(t) for t in cell.get("row_confirmed", [])),
                "row_review_types": ", ".join(formula_safe(t) for t in cell.get("row_possible", [])),
                "row_basis": formula_safe(cell.get("row_basis", "")),
                "row_risk": formula_safe(cell.get("row_risk", "")),
                "ai_row": formula_safe(cell.get("row_ai", {}).get("personality", "")),
            }


def iter_row_export_records(file_results: Iterable) -> Iterable[dict]:
    """Stream the export-ready row records (headers in ROW_HEADERS order)."""
    for result in file_results:
        analysed_at, file_name, db_type, schema, table = _file_meta(result)
        for record in iter_row_records([result]):
            yield {
                "analysed_at": analysed_at,
                "file": file_name,
                "db_type": db_type,
                "schema": schema,
                "table": table,
                "row_number": record["row"],
                "status": formula_safe(record.get("status", "없음")),
                "confirmed_pii": ", ".join(record.get("confirmed_pii", [])),
                "possible_pii": ", ".join(record.get("possible_pii", [])),
                "has_pii": "예" if record.get("has_pii") else "아니요",
                # Legacy aliases kept for readers that expect the old key names;
                # the CSV columns themselves come from ROW_FIELDS.
                "pii_types": ", ".join(record.get("confirmed_pii", [])),
                "review_types": ", ".join(record.get("possible_pii", [])),
                "columns": ", ".join(formula_safe(name) for name in record.get("column_names", [])),
                "column_indexes": ", ".join(str(index) for index in record.get("column_indexes", [])),
                "cell_count": record.get("cell_count", 0),
                "basis": formula_safe(record.get("basis", "")),
                "confidence": round(record.get("confidence", 0.0), 3),
                "risk_level": formula_safe(record.get("risk_level", "")),
                "needs_review": "예" if record.get("needs_review") else "아니요",
                "ai_row": formula_safe(record.get("ai_review", {}).get("personality", "")),
            }


def collect_hit_cells(file_results: Iterable) -> list[dict]:
    """Materialised cell detail - kept for tests and small result sets."""
    return list(iter_hit_cells(file_results))


def collect_row_records(file_results: Iterable) -> list[dict]:
    """Materialised row detail - kept for tests and small result sets."""
    return list(iter_row_export_records(file_results))


def _write(rows: Iterable[dict], fields: list[tuple[str, str]], output: str | Path) -> int:
    """Write records one at a time, emitting exactly the header columns.

    Columns come from `fields`, not from the record key order, so a record that
    gains or loses a key can never shift the columns under the header.
    """
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with open(output, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([header for _, header in fields])
        for row in rows:
            writer.writerow([row.get(key, "") for key, _ in fields])
            count += 1
    return count


def write_hit_rows(file_results: Iterable, output: str | Path) -> str:
    """Cell-level detail, streamed straight out of SQLite (same entry point)."""
    write_hit_rows.rows_written = _write(iter_hit_cells(file_results), CELL_FIELDS, output)
    return str(output)


# Backwards-compatible alias for the previous name.
collect_hit_rows = collect_hit_cells


def write_row_records(file_results: Iterable, output: str | Path) -> str:
    """Row-level summary: confirmed personal data vs data that still needs review."""
    write_row_records.rows_written = _write(iter_row_export_records(file_results), ROW_FIELDS, output)
    return str(output)
