"""결과 CSV 출력. 원본 개인정보 값은 기록하지 않는다."""
from __future__ import annotations

import csv
from pathlib import Path

from app.utils.csv_safety import formula_safe

HEADERS = [
    "분석일시", "파일명", "원본DB", "스키마", "테이블명", "분석방식", "분석행수",
    "컬럼명", "컬럼 인덱스", "개인정보유형", "판정", "탐지건수", "탐지비율", "탐지행(예, 마스킹)",
    "암호화관련컬럼", "마스킹상태", "해시상태", "규칙판정", "AI판정", "판정일치",
    "최종판정", "규칙신뢰도", "AI신뢰도", "위험도", "판단근거", "추가확인",
]


def write_csv(results: list, output: str | Path) -> str:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADERS)
        for result in results:
            row = result.as_dict() if hasattr(result, "as_dict") else result
            writer.writerow([
                formula_safe(row["analysed_at"]),
                formula_safe(row["file"]),
                formula_safe(row["db_type"]),
                formula_safe(row["schema"]),
                formula_safe(row["table"]),
                formula_safe(row["mode"]),
                row["rows_analysed"],
                formula_safe(row["column"]),
                row.get("column_index", 0),
                formula_safe(row["display_name"]),
                formula_safe(row.get("verdict", "개인정보")),
                row["hits"],
                row["ratio"],
                formula_safe(row.get("hit_rows_text", "")),
                formula_safe(row["protection_columns"]),
                formula_safe(row["mask_status"]),
                formula_safe(row["hash_status"]),
                formula_safe(row.get("rule_decision", "")),
                formula_safe(row.get("ai_decision", "")),
                formula_safe(row.get("agreement", "AI 미사용")),
                formula_safe(row.get("final_decision", row["privacy_type"])),
                row["rule_confidence"],
                row["ai_confidence"],
                formula_safe(row["risk_level"]),
                formula_safe(row["rationale"]),
                "Y" if row["requires_review"] else "N",
            ])
    return str(output)
