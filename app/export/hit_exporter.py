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

CELL_HEADERS = [
    "분석일시", "파일명", "원본DB", "스키마", "테이블명",
    "행번호", "컬럼명", "컬럼 인덱스", "개인정보유형", "판정",
    "탐지값(마스킹)", "판단근거", "Cell 신뢰도", "검토 필요",
    "Row 판정", "Row 확정 유형", "Row 검토 유형", "Row 근거", "Row 위험도", "AI Row 판정",
]

ROW_HEADERS = [
    "분석일시", "파일명", "원본DB", "스키마", "테이블명",
    "행번호", "개인정보 상태", "확정 개인정보", "검토 필요 개인정보",
    "개인정보 존재", "발생 컬럼", "컬럼 인덱스", "탐지 Cell 수", "판정 근거",
    "Row 신뢰도", "Row 위험도", "검토 필요", "AI Row 판정",
]

from app.utils.csv_safety import formula_safe  # re-exported for the QA tests


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
            for raw in store.iter_rows():
                yield raw
        else:
            for record in getattr(result, "row_records", None) or []:
                yield record.as_dict()


def collect_hit_cells(file_results: Iterable) -> list[dict]:
    """One entry per detected cell, ordered by row then column index."""
    rows: list[dict] = []
    for result in file_results:
        analysed_at, file_name, db_type, schema, table = _file_meta(result)
        store = getattr(result, "detail_store", None)
        if store is not None:
            cells = store.iter_cells_with_row_context()
        else:
            cells = [
                {**cell.as_dict(), "row_status": record.status,
                 "row_confirmed": record.confirmed_pii, "row_possible": record.possible_pii,
                 "row_basis": record.basis, "row_risk": record.risk_level, "row_ai": record.ai_review}
                for record in getattr(result, "row_records", None) or [] for cell in record.cells
            ]
        for cell in cells:
            rows.append({
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
                "row_verdict": cell.get("row_status", "검토 필요"),
                "row_types": ", ".join(cell.get("row_confirmed", []) + cell.get("row_possible", [])),
                "row_basis": formula_safe(cell.get("row_basis", "")),
                "row_risk": formula_safe(cell.get("row_risk", "")),
                "ai_row": formula_safe(cell.get("row_ai", {}).get("personality", "")),
            })
    return rows


def collect_row_records(file_results: Iterable) -> list[dict]:
    """One entry per row that produced at least one cell hit."""
    rows: list[dict] = []
    for result in file_results:
        analysed_at, file_name, db_type, schema, table = _file_meta(result)
        for record in iter_row_records(file_results=[result]):
            has_ai = bool(record.get("ai_review"))
            rows.append({
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
            })
    return rows


def _write(rows: list[dict], headers: list[str], output: str | Path) -> str:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        for row in rows:
            writer.writerow([row[key] for key in row])
    return str(output)


def write_hit_rows(file_results: Iterable, output: str | Path) -> str:
    """Cell-level detail (same entry point as before)."""
    return _write(collect_hit_cells(file_results), CELL_HEADERS, output)


# Backwards-compatible alias for the previous name.
collect_hit_rows = collect_hit_cells


def write_row_records(file_results: Iterable, output: str | Path) -> str:
    """Row-level summary: confirmed personal data vs data that still needs review."""
    return _write(collect_row_records(file_results), ROW_HEADERS, output)
