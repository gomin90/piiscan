"""Row- and cell-level detection detail export.

Two views of the same evidence:

* `privacy_hits_*.csv` - one line per detected **cell**: source row, column name,
  column index, privacy type, masked value, the cell verdict and the rule evidence
  behind it, plus the row context that produced the row-level verdict.
* `privacy_rows_*.csv` - one line per **row** that contained at least one hit:
  whether the record holds personal data, which types, in which columns, and why.

Raw personal values never reach these files: every value stored in the engine is
already the output of `mask_value()`.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

CELL_HEADERS = [
    "분석일시", "파일명", "원본DB", "스키마", "테이블명",
    "행번호", "컬럼명", "컬럼 인덱스", "개인정보유형", "판정",
    "탐지값(마스킹)", "판단근거", "Cell 신뢰도", "검토 필요",
    "Row 판정", "Row 유형", "Row 근거", "Row 위험도", "AI Row 판정",
]

ROW_HEADERS = [
    "분석일시", "파일명", "원본DB", "스키마", "테이블명",
    "행번호", "개인정보 존재", "개인정보 유형", "검토 유형",
    "발생 컬럼", "컬럼 인덱스", "탐지 Cell 수", "판정 근거",
    "Row 신뢰도", "Row 위험도", "검토 필요", "AI Row 판정",
]


def _file_meta(result) -> tuple[str, str, str, str, str]:
    settings = result.settings if isinstance(result.settings, dict) else {}
    return (
        settings.get("analysed_at", ""),
        Path(result.path).name,
        settings.get("db_type", ""),
        settings.get("schema", ""),
        settings.get("table", Path(result.path).stem),
    )


def collect_hit_cells(file_results: Iterable) -> list[dict]:
    """One entry per detected cell, ordered by row then column index."""
    rows: list[dict] = []
    for result in file_results:
        analysed_at, file_name, db_type, schema, table = _file_meta(result)
        for record in getattr(result, "row_records", None) or []:
            for cell in record.cells:
                rows.append({
                    "analysed_at": analysed_at,
                    "file": file_name,
                    "db_type": db_type,
                    "schema": schema,
                    "table": table,
                    "row_number": record.row,
                    "column": cell.column,
                    "column_index": cell.column_index,
                    "privacy_type": cell.type_key,
                    "verdict": cell.decision or "개인정보",
                    "masked_value": cell.masked_value,
                    "evidence": cell.evidence,
                    "confidence": round(cell.confidence, 3),
                    "needs_review": "예" if cell.needs_review else "아니요",
                    "row_verdict": "개인정보" if record.has_pii else "없음",
                    "row_types": ", ".join(record.pii_types + record.review_types),
                    "row_basis": record.basis,
                    "row_risk": record.risk_level,
                    "ai_row": record.ai_review.get("personality", "") if record.ai_review else "",
                })
    return rows


def collect_row_records(file_results: Iterable) -> list[dict]:
    """One entry per row that produced at least one cell hit."""
    rows: list[dict] = []
    for result in file_results:
        analysed_at, file_name, db_type, schema, table = _file_meta(result)
        for record in getattr(result, "row_records", None) or []:
            rows.append({
                "analysed_at": analysed_at,
                "file": file_name,
                "db_type": db_type,
                "schema": schema,
                "table": table,
                "row_number": record.row,
                "has_pii": "예" if record.has_pii else "아니요",
                "pii_types": ", ".join(record.pii_types),
                "review_types": ", ".join(record.review_types),
                "columns": ", ".join(record.column_names),
                "column_indexes": ", ".join(str(index) for index in record.column_indexes),
                "cell_count": len(record.cells),
                "basis": record.basis,
                "confidence": round(record.confidence, 3),
                "risk_level": record.risk_level,
                "needs_review": "예" if record.needs_review else "아니요",
                "ai_row": record.ai_review.get("personality", "") if record.ai_review else "",
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
    """Row-level summary: does this record contain personal data, and where."""
    return _write(collect_row_records(file_results), ROW_HEADERS, output)
