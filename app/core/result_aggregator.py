"""규칙 결과 + AI 결과 + 보호 분석을 하나의 결과 행(rows)으로 통합."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.core.risk_engine import assess_column


@dataclass
class ColumnResult:
    analysed_at: str
    file: str
    db_type: str
    schema: str
    table: str
    mode: str
    rows_analysed: int
    column: str
    privacy_type: str
    display_name: str
    hits: int
    analysed_rows: int
    ratio: float
    protection_columns: list
    mask_status: str
    hash_status: str
    method: str
    rule_confidence: float
    ai_confidence: float
    risk_level: str
    risk_score: float
    rationale: str
    requires_review: bool

    def as_dict(self) -> dict:
        return {
            "analysed_at": self.analysed_at,
            "file": self.file,
            "db_type": self.db_type,
            "schema": self.schema,
            "table": self.table,
            "mode": self.mode,
            "rows_analysed": self.rows_analysed,
            "column": self.column,
            "privacy_type": self.privacy_type,
            "display_name": self.display_name,
            "hits": self.hits,
            "analysed_rows": self.analysed_rows,
            "ratio": self.ratio,
            "protection_columns": ", ".join(self.protection_columns),
            "mask_status": self.mask_status,
            "hash_status": self.hash_status,
            "method": self.method,
            "rule_confidence": self.rule_confidence,
            "ai_confidence": self.ai_confidence,
            "risk_level": self.risk_level,
            "risk_score": self.risk_score,
            "rationale": self.rationale,
            "requires_review": self.requires_review,
        }


def merge_protection(protection_groups) -> dict:
    """Base column name -> protection detail, plus orphan protection columns."""
    mapping = {}
    for group in protection_groups:
        parts = [part for part in (group.mask, group.hash_, group.enc) if part]
        mapping[group.base] = {
            "columns": parts,
            "mask": group.mask or {},
            "hash": group.hash_ or {},
            "enc": group.enc or {},
            "status": group.status,
            "status_score": group.status_score,
            "review_required": group.review_required,
        }
        for column_name, detail in group.related.items():
            mapping[column_name] = {
                "columns": [detail],
                "mask": {},
                "hash": {},
                "enc": {},
                "status": detail.get("evidence", ""),
                "status_score": 0.0,
                "review_required": True,
            }
    return mapping


def _protection_column_names(group: dict) -> list:
    return [part.get("column") for part in group.get("columns", []) if isinstance(part, dict) and part.get("column")]


def build_results(
    file: str,
    db_type: str,
    schema: str,
    table: str,
    mode: str,
    rows_analysed: int,
    findings: list,
    protection_groups: list,
    ai_judgements: dict,
) -> list:
    """One result row per (column, privacy type). AI never overwrites rule evidence."""
    protection = merge_protection(protection_groups)
    pii_columns = [finding for finding in findings if finding.get("types")]
    table_pii_types = len({t["type_key"] for f in pii_columns for t in f["types"]})
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    results = []
    for finding in findings:
        ai = ai_judgements.get(finding["column"])
        group = protection.get(finding["column"], {})
        assessment = assess_column(finding, group, ai, table_pii_types)
        mask_status = "확인" if group.get("mask", {}).get("verified") else ("존재(미검)" if group.get("mask") else "없음")
        hash_status = "확인" if group.get("hash", {}).get("verified") else ("존재(미검)" if group.get("hash") else "없음")
        protection_names = _protection_column_names(group)

        if finding["types"]:
            for type_info in finding["types"]:
                results.append(
                    ColumnResult(
                        analysed_at=now,
                        file=file,
                        db_type=db_type,
                        schema=schema,
                        table=table,
                        mode=mode,
                        rows_analysed=rows_analysed,
                        column=finding["column"],
                        privacy_type=type_info["type_key"],
                        display_name=type_info["display_name"],
                        hits=type_info["hits"],
                        analysed_rows=finding["non_empty"],
                        ratio=round(type_info["ratio"], 4),
                        protection_columns=protection_names,
                        mask_status=mask_status,
                        hash_status=hash_status,
                        method="규칙+AI" if ai else "규칙",
                        rule_confidence=type_info["rule_confidence"],
                        ai_confidence=ai.get("confidence", 0.0) if ai else 0.0,
                        risk_level=assessment["level"],
                        risk_score=assessment["score"],
                        rationale=" | ".join(assessment["drivers"] + [type_info["evidence"]]),
                        requires_review=(not type_info["confirmed"]) or bool(ai and ai.get("requires_review")),
                    )
                )
        elif ai and ai.get("privacy_type") != "NONE":
            results.append(
                ColumnResult(
                    analysed_at=now,
                    file=file,
                    db_type=db_type,
                    schema=schema,
                    table=table,
                    mode=mode,
                    rows_analysed=rows_analysed,
                    column=finding["column"],
                    privacy_type=ai["privacy_type"],
                    display_name=ai["privacy_type"],
                    hits=0,
                    analysed_rows=finding["non_empty"],
                    ratio=0.0,
                    protection_columns=protection_names,
                    mask_status=mask_status,
                    hash_status=hash_status,
                    method="AI(보조)",
                    rule_confidence=0.0,
                    ai_confidence=ai.get("confidence", 0.0),
                    risk_level=assessment["level"],
                    risk_score=assessment["score"],
                    rationale=" | ".join(assessment["drivers"] + [ai.get("reason", "")]),
                    requires_review=True,
                )
            )
    results.sort(key=lambda row: (-row.risk_score, row.column))
    return results
