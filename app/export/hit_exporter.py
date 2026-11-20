"""행 단위 탐지 상세 출력.

One line per rule hit: which source row matched, the masked value, and the
verdict. Raw personal values are never written here - every sample goes
through `mask_value` before it reaches a report.
"""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

HEADERS = [
    "분석일시", "파일명", "원본DB", "스키마", "테이블명",
    "컬럼명", "개인정보유형", "판정", "행번호", "탐지값(마스킹)",
    "탐지건수", "분석행수", "탐지비율", "규칙신뢰도", "판단근거",
]

ROW_LIMIT_PER_TYPE = 50


def collect_hit_rows(file_results: list) -> list[dict]:
    """Flatten every rule hit into one row per (column, type, source row)."""
    rows: list[dict] = []
    for result in file_results:
        settings = result.settings if isinstance(result.settings, dict) else {}
        for finding in result.findings:
            for type_info in list(finding.get("types", [])) + list(finding.get("weak_types", [])):
                for hit_row in type_info.get("hit_rows", [])[:ROW_LIMIT_PER_TYPE]:
                    rows.append(
                        {
                            "analysed_at": result.settings.get("analysed_at", ""),
                            "file": Path(result.path).name,
                            "db_type": settings.get("db_type", ""),
                            "schema": settings.get("schema", ""),
                            "table": settings.get("table", Path(result.path).stem),
                            "column": finding["column"],
                            "display_name": type_info["display_name"],
                            "verdict": type_info.get("decision", "개인정보"),
                            "row_number": hit_row["row"],
                            "masked_value": hit_row["value"],
                            "total_hits": type_info["hits"],
                            "analysed_rows": finding["non_empty"],
                            "ratio": type_info["ratio"],
                            "rule_confidence": type_info["rule_confidence"],
                            "rationale": type_info.get("evidence", ""),
                        }
                    )
    return rows


def write_hit_rows(file_results: list, output: str | Path) -> str:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = collect_hit_rows(file_results)
    with open(output, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADERS)
        for row in rows:
            writer.writerow([
                row["analysed_at"], row["file"], row["db_type"], row["schema"], row["table"],
                row["column"], row["display_name"], row["verdict"], row["row_number"],
                row["masked_value"], row["total_hits"], row["analysed_rows"], row["ratio"],
                row["rule_confidence"], row["rationale"],
            ])
    return str(output)
