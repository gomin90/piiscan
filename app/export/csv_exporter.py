"""결과 CSV 출력. 원본 개인정보 값은 기록하지 않는다."""
from __future__ import annotations

import csv
from pathlib import Path

HEADERS = [
    "분석일시", "파일명", "원본DB", "스키마", "테이블명", "분석방식", "분석행수",
    "컬럼명", "개인정보유형", "탐지건수", "탐지비율", "암호화관련컬럼",
    "마스킹상태", "해시상태", "규칙신뢰도", "AI신뢰도", "위험도", "판단근거", "추가확인",
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
                row["analysed_at"],
                row["file"],
                row["db_type"],
                row["schema"],
                row["table"],
                row["mode"],
                row["rows_analysed"],
                row["column"],
                row["display_name"],
                row["hits"],
                row["ratio"],
                row["protection_columns"],
                row["mask_status"],
                row["hash_status"],
                row["rule_confidence"],
                row["ai_confidence"],
                row["risk_level"],
                row["rationale"],
                "Y" if row["requires_review"] else "N",
            ])
    return str(output)
