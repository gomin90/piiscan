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

from app.config.privacy_policy import get_policy
from app.utils.csv_safety import formula_safe

# (record key, CSV column). `_write` emits exactly these columns in this order,
# so a record can never drift out of alignment with its header row.
CELL_FIELDS = [
    ("analysed_at", "분석일시"), ("file", "파일명"), ("db_type", "원본DB"),
    ("schema", "스키마"), ("table", "테이블명"), ("row_number", "행번호"),
    ("column", "컬럼명"), ("column_index", "컬럼 인덱스"),
    ("privacy_type", "개인정보유형"), ("tier", "우선순위(Tier)"),
    ("category", "분류"), ("occurrence", "Occurrence"),
    ("match_position", "Match 위치"), ("verdict", "판정"),
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
    ("line_start", "원본 시작 라인"), ("line_end", "원본 끝 라인"),
]

# Every analysed cell, settled or not: the complete Cell-level ledger.
VERDICT_FIELDS = [
    ("analysed_at", "분석일시"), ("file", "파일명"), ("db_type", "원본DB"),
    ("schema", "스키마"), ("table", "테이블명"), ("row_number", "행번호"),
    ("column", "컬럼명"), ("column_index", "컬럼 인덱스"),
    ("cell_status", "Cell 판정"), ("privacy_types", "개인정보 유형"),
    ("rule_verdict", "규칙 판정"), ("ai_verdict", "AI 판정"),
    ("final_verdict", "최종 판정"), ("confidence", "Cell 신뢰도"),
    ("decision_method", "판단 방법"), ("needs_review", "검토 필요"),
    ("masked_value", "마스킹 값"), ("evidence", "판단 근거"),
    ("tier", "우선순위(Tier)"),
    ("occurrences", "Occurrence 수"),
    ("line_start", "원본 시작 라인"), ("line_end", "원본 끝 라인"),
]

CELL_HEADERS = [header for _, header in CELL_FIELDS]
ROW_HEADERS = [header for _, header in ROW_FIELDS]
VERDICT_HEADERS = [header for _, header in VERDICT_FIELDS]

# Function A: one line per source record (Row identity + its Cell verdict mix).
RECORD_FIELDS = [
    ("analysed_at", "분석일시"), ("file", "파일명"), ("db_type", "원본DB"),
    ("schema", "스키마"), ("table", "테이블명"), ("row_number", "행번호"),
    ("row_key", "Row 식별자"), ("key_fingerprint", "KEY 지문(HMAC)"),
    ("total_cells", "Cell 수"), ("key_cells", "KEY 제외 Cell"),
    ("empty_cells", "빈 Cell"), ("pii_cells", "개인정보 Cell"),
    ("review_cells", "검토 Cell"), ("non_pii_cells", "비개인정보 Cell"),
    ("unrated_cells", "미평가 Cell"), ("row_verdict", "Row 판정"),
    ("has_pii", "개인정보 존재"),
]

# Function A: identical personal-data values, grouped by HMAC fingerprint.
GROUP_FIELDS = [
    ("analysed_at", "분석일시"), ("file", "파일명"), ("db_type", "원본DB"),
    ("schema", "스키마"), ("table", "테이블명"),
    ("masked_sample", "개인정보 샘플(마스킹)"), ("fingerprint", "개인정보 지문(HMAC)"),
    ("privacy_types", "개인정보 유형"),
    ("occurrences", "발생 횟수"), ("distinct_rows", "Row 수"),
    ("distinct_records", "KEY 레코드 수"), ("columns", "발생 컬럼"),
    ("row_keys", "Related Row"), ("first_row", "첫 발생 Row"),
    ("last_row", "마지막 Row"),
]

# Re-exported for the QA tests.
__all__ = ["CELL_HEADERS", "ROW_HEADERS", "VERDICT_HEADERS", "RECORD_FIELDS", "GROUP_FIELDS",
           "CELL_FIELDS", "ROW_FIELDS", "VERDICT_FIELDS", "formula_safe", "iter_hit_cells", "iter_cell_records",
           "iter_row_records", "iter_row_export_records", "collect_hit_cells",
           "collect_row_records", "write_hit_rows", "write_cell_records",
           "write_row_records", "write_record_records", "write_group_records", "collect_hit_rows"]


def _file_meta(result) -> tuple[str, str, str, str, str]:
    settings = result.settings if isinstance(result.settings, dict) else {}
    return (
        settings.get("analysed_at", ""),
        formula_safe(Path(result.path).name),
        formula_safe(settings.get("db_type", "")),
        formula_safe(settings.get("schema", "")),
        formula_safe(settings.get("table", Path(result.path).stem)),
    )


def _types_text(value) -> str:
    """Render the multi-type cell column without splitting a stored string into characters."""
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",") if part.strip()]
    else:
        parts = [str(part).strip() for part in (value or []) if str(part).strip()]
    return ", ".join(formula_safe(part) for part in dict.fromkeys(parts))


def _category_of(type_text: str) -> str:
    """Legal category of the first recognised type named in a merged Cell."""
    policy = get_policy()
    for part in (type_text or "").split(","):
        code = part.strip()
        if not code:
            continue
        ptype = policy.get(code) or next(
            (item for item in policy.types.values() if item.display_name_ko == code), None)
        if ptype is not None:
            return ptype.category
    return ""


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
        cells = (store.iter_hit_cell_records() if store is not None
                 else _fallback_cells(result))
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
                "tier": cell.get("tier") or cell.get("priority") or 2,
                "category": formula_safe(_category_of(cell["type_key"])),
                "occurrence": cell.get("occurrence_index") or 1,
                "match_position": formula_safe(
                    cell.get("spans")
                    or ("" if cell.get("match_start", -1) < 0
                        else f"{cell['match_start']}-{cell['match_end']}")),
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
                "line_start": record.get("line_start") or record["row"],
                "line_end": record.get("line_end") or record["row"],
            }



def iter_record_records(file_results: Iterable) -> Iterable[dict]:
    """Stream one line per source row (KEY identity + Cell verdict mix)."""
    for result in file_results:
        analysed_at, file_name, db_type, schema, table = _file_meta(result)
        store = getattr(result, "detail_store", None)
        if store is None:
            continue
        for record in store.iter_records():
            yield {
                "analysed_at": analysed_at,
                "file": file_name,
                "db_type": db_type,
                "schema": schema,
                "table": table,
                "row_number": record["row"],
                "row_key": formula_safe(record["row_key"]),
                "key_fingerprint": record["key_fingerprint"] or "(KEY 없음)",
                "total_cells": record["total_cells"],
                "key_cells": record["key_cells"],
                "empty_cells": record["empty_cells"],
                "pii_cells": record["pii_cells"],
                "review_cells": record["review_cells"],
                "non_pii_cells": record["non_pii_cells"],
                "unrated_cells": record["unrated_cells"],
                "row_verdict": formula_safe(record["status"]),
                "has_pii": "예" if record["has_pii"] else "아니요",
                "line_start": record.get("line_start") or record["row"],
                "line_end": record.get("line_end") or record["row"],
            }


def iter_group_records(file_results: Iterable) -> Iterable[dict]:
    """Stream the identical-value groups; the raw value is never in the output."""
    for result in file_results:
        analysed_at, file_name, db_type, schema, table = _file_meta(result)
        store = getattr(result, "detail_store", None)
        if store is None:
            continue
        for group in store.iter_groups():
            yield {
                "analysed_at": analysed_at,
                "file": file_name,
                "db_type": db_type,
                "schema": schema,
                "table": table,
                "masked_sample": formula_safe(group.get("masked_sample") or ""),
                "fingerprint": group["fingerprint"],
                "privacy_types": ", ".join(formula_safe(t) for t in
                                          dict.fromkeys(group["pii_type"].split(",")) if t),
                "occurrences": group["occurrences"],
                "distinct_rows": group["distinct_rows"],
                "distinct_records": group["distinct_records"],
                "columns": ", ".join(formula_safe(c) for c in group["columns"]),
                "row_keys": ", ".join(formula_safe(k) for k in group["key_samples"]),
                "first_row": group["first_row"],
                "last_row": group["last_row"],
            }

def collect_hit_cells(file_results: Iterable) -> list[dict]:
    """Materialised cell detail - kept for tests and small result sets."""
    return list(iter_hit_cells(file_results))


def iter_cell_records(file_results: Iterable) -> Iterable[dict]:
    """Stream the verdict of *every* analysed cell, one CSV line per cell.

    This is the complete Cell ledger: matched, cleared, undecided and empty cells
    all appear, so the row and column summaries can always be rebuilt from it.
    """
    for result in file_results:
        analysed_at, file_name, db_type, schema, table = _file_meta(result)
        store = getattr(result, "detail_store", None)
        if store is None:
            continue
        for cell in store.iter_cell_verdicts():
            types = _types_text(cell.get("privacy_types", ""))
            yield {
                "analysed_at": analysed_at,
                "file": file_name,
                "db_type": db_type,
                "schema": schema,
                "table": table,
                "row_number": cell["row"],
                "column": formula_safe(cell["column"]),
                "column_index": cell["column_index"],
                "cell_status": formula_safe(cell["cell_status"]),
                "privacy_types": types,
                "rule_verdict": formula_safe(cell.get("rule_verdict", "")),
                "ai_verdict": formula_safe(cell.get("ai_verdict", "")),
                "final_verdict": formula_safe(cell.get("final_verdict") or cell["cell_status"]),
                "confidence": round(cell.get("confidence", 0.0), 3),
                "decision_method": formula_safe(cell.get("decision_method", "")),
                "needs_review": "예" if cell.get("needs_review") else "아니요",
                "masked_value": formula_safe(cell.get("masked_value", "")),
                "evidence": formula_safe(cell.get("evidence", "")),
            }


def collect_cell_records(file_results: Iterable) -> list[dict]:
    """Materialised Cell ledger - tests and small result sets only."""
    return list(iter_cell_records(file_results))


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


def write_record_records(file_results: Iterable, output: str | Path) -> str:
    """KEY-based record report: every source row and its Cell verdict mix."""
    write_record_records.rows_written = _write(iter_record_records(file_results), RECORD_FIELDS, output)
    return str(output)


def write_group_records(file_results: Iterable, output: str | Path) -> str:
    """Identical personal-data values grouped by HMAC fingerprint."""
    write_group_records.rows_written = _write(iter_group_records(file_results), GROUP_FIELDS, output)
    return str(output)


# Backwards-compatible alias for the previous name.
collect_hit_rows = collect_hit_cells


def write_row_records(file_results: Iterable, output: str | Path) -> str:
    """Row-level summary: confirmed personal data vs data that still needs review."""
    write_row_records.rows_written = _write(iter_row_export_records(file_results), ROW_FIELDS, output)
    return str(output)


def write_cell_records(file_results: Iterable, output: str | Path) -> str:
    """Complete Cell ledger: one line for every analysed cell, matched or not."""
    write_cell_records.rows_written = _write(iter_cell_records(file_results), VERDICT_FIELDS, output)
    return str(output)
