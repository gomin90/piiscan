"""Function B reports: streamed CSV/TXT written straight from SQLite."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

from app.metadata_analysis.column_classifier import LOW, RELEVANT, REVIEW
from app.utils.csv_safety import formula_safe

COLUMN_FIELDS = ("table_name", "schema_name", "column_name", "column_comment", "data_type",
                 "column_length", "primary_key", "foreign_key", "verdict", "relevance",
                 "type_candidates", "method", "rationale", "needs_confirmation")
TABLE_FIELDS = ("table_name", "status", "relevance", "pii_column_count",
                "candidate_types", "column_count", "rationale")
REVIEW_FIELDS = ("table_name", "column_name", "data_type", "column_comment", "verdict",
                 "relevance", "type_candidates", "action")

ACTION_BY_VERDICT = {
    RELEVANT: "개인정보 관리 대상 - 실제 값 존재 여부 확인",
    REVIEW: "검토 필요 - 샘플 데이터로 확인",
    LOW: "관련 낮음 - 값 확인 후 제외 검토",
}


def _safe(value) -> str:
    text = "" if value is None else str(value)
    return formula_safe(text)


def iter_column_verdicts(store) -> Iterable[tuple]:
    cursor = store.conn.execute(
        "SELECT m.table_name, m.schema_name, m.column_name, m.column_comment, m.data_type, "
        "m.length, m.primary_key, m.foreign_key, v.verdict, v.confidence, v.type_candidates, "
        "v.method, v.rationale, v.needs_confirmation "
        "FROM column_verdicts v JOIN meta_records m "
        "  ON m.analysis_id = v.analysis_id AND m.table_name = v.table_name "
        " AND m.column_name = v.column_name "
        "WHERE v.analysis_id = ? ORDER BY v.confidence DESC, m.table_name, m.column_name",
        (store.analysis_id,),
    )
    while True:
        row = cursor.fetchone()
        if row is None:
            break
        yield row


def write_metadata_columns(store, path: str | Path) -> int:
    path = Path(path)
    count = 0
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMN_FIELDS)
        for row in iter_column_verdicts(store):
            writer.writerow([_safe(value) for value in row])
            count += 1
    return count


def write_metadata_tables(store, path: str | Path) -> int:
    path = Path(path)
    count = 0
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(TABLE_FIELDS)
        cursor = store.conn.execute(
            "SELECT table_name, status, relevance, pii_column_count, "
            "candidate_types, column_count, rationale "
            "FROM table_verdicts WHERE analysis_id = ? ORDER BY relevance DESC, table_name",
            (store.analysis_id,),
        )
        while True:
            row = cursor.fetchone()
            if row is None:
                break
            writer.writerow([_safe(value) for value in row])
            count += 1
    return count


def write_metadata_review(store, path: str | Path) -> int:
    """Columns that still need a human: relevance without confirmation."""
    path = Path(path)
    count = 0
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(REVIEW_FIELDS)
        cursor = store.conn.execute(
            "SELECT m.table_name, m.column_name, m.data_type, m.column_comment, v.verdict, "
            "v.confidence, v.type_candidates FROM column_verdicts v JOIN meta_records m "
            "  ON m.analysis_id = v.analysis_id AND m.table_name = v.table_name "
            " AND m.column_name = v.column_name "
            "WHERE v.analysis_id = ? AND v.needs_confirmation = 1 "
            "ORDER BY v.confidence DESC, m.table_name, m.column_name",
            (store.analysis_id,),
        )
        while True:
            row = cursor.fetchone()
            if row is None:
                break
            verdict = row[4]
            writer.writerow([_safe(value) for value in row] + [ACTION_BY_VERDICT.get(verdict, "검토")])
            count += 1
    return count


def write_metadata_summary(store, path: str | Path, summary: dict) -> None:
    path = Path(path)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("Privacy Scanner - DB 메타정보 분석 보고서\n")
        handle.write("=" * 52 + "\n")
        for key, value in summary.items():
            handle.write(f"{key}: {value}\n")
        if summary.get("rows_excluded") or summary.get("columns_excluded"):
            handle.write(
                f"\n경고: 상한(max_rows/max_cols) 초과로 Row {summary.get('rows_excluded', 0):,}건 / "
                f"Col {summary.get('columns_excluded', 0)}건은 분석되지 않았다. "
                f"이 결과는 전수 분석이 아니다.\n"
            )
        handle.write("\n주의: 메타정보만으로 개인정보 존재를 확정할 수 없습니다.\n")
        handle.write(" 모든 '개인정보 관련' 항목은 실제 값 확인 후 관리 대상에 포함해야 합니다.\n")
