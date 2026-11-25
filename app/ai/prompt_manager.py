"""Local LLM 프롬프트 조립: 원문 개인정보를 전달하지 않는다."""
from __future__ import annotations

import json

from app.utils.masking import mask_value

SYSTEM_PROMPT = (
    "당신은 개인정보 탐지 보조 분석기입니다. 주어진 컬럼 통계만 근거하여 "
    "개인정보 유형을 판정하고 JSON만 반환합니다. 원문 값은 제공되지 않습니다. "
    '응답 형식: {"column": str, "privacy_type": str, "confidence": float, '
    '"reason": str, "requires_review": bool}'
)

from app.config.privacy_policy import get_policy

#: The model may only name types the rule engine actually understands, so the
# allowed list is derived from the privacy policy rather than hardcoded here.
VALID_TYPES = sorted({code for code in get_policy().types if code != "MASKED"} | {"NONE"})


def column_context(column: dict) -> dict:
    """Build a privacy-safe LLM context: statistics and masked samples only."""
    stats = column.get("stats", {})
    return {
        "column": column.get("column"),
        "table": column.get("table", ""),
        "header_kind": "named" if column.get("name_affinity", 0) else "code",
        "non_empty": stats.get("non_empty", 0),
        "null_ratio": round(stats.get("null", 0) / max(stats.get("rows", 1), 1), 3),
        "masked_like": stats.get("masked_like", 0),
        "numeric_ratio": stats.get("numeric_ratio", 0),
        "distinct_estimate": stats.get("distinct_estimate", 0),
        "length": {
            "min": stats.get("min_len", 0),
            "max": stats.get("max_len", 0),
            "avg": stats.get("avg_len", 0),
        },
        "rule_hits": [
            {"type": t["display_name"], "hits": t["hits"], "ratio": t["ratio"]}
            for t in column.get("types", [])
        ],
        "masked_samples": [mask_value(s) for s in stats.get("samples", [])[:5]],
    }


def build_prompt(context: dict) -> str:
    return SYSTEM_PROMPT + "\n\n분석 대상:\n" + json.dumps(context, ensure_ascii=False, indent=2)


def _ranked_cells(cells: list[dict], limit: int = 12) -> list[dict]:
    """Confirmed evidence first, then one cell per distinct type, then the rest.

    Taking the first N cells of a row would let column order decide what the model
    sees; ranking by verdict and confidence keeps the settled evidence and the
    ambiguous evidence both visible, and keeps distinct types represented.
    """
    confirmed = [c for c in cells if c.get("decision") == "개인정보"]
    review = [c for c in cells if c.get("decision") == "검토 필요"]
    confirmed.sort(key=lambda c: (-c.get("confidence", 0.0), c.get("column_index", 0)))
    review.sort(key=lambda c: (-c.get("confidence", 0.0), c.get("column_index", 0)))

    seen: set[str] = set()
    diverse: list[dict] = []
    for cell in confirmed + review:
        key = cell.get("type_key") or cell.get("display_name") or ""
        if key not in seen:
            seen.add(key)
            diverse.append(cell)
    ordered = diverse + [c for c in confirmed + review if c not in diverse]
    return ordered[:limit]


def row_context(record: dict) -> dict:
    """Row-level LLM context: masked cell values and rule evidence only."""
    return {
        "row": record.get("row"),
        "schema": record.get("schema", ""),
        "table": record.get("table", ""),
        "risk_level": record.get("risk_level", ""),
        "confirmed_types": record.get("confirmed_types", []),
        "possible_types": record.get("possible_types", []),
        "cells_checked": record.get("cells_checked", 0),
        # The KEY columns identify the record; the model may see them only in
        # masked form, which is what the store keeps (spec section 4).
        "key_columns": [part for part in (record.get("key_mask") or "").split(" | ") if part],
        "source_line": record.get("line_start") or record.get("row"),
        "cells": [
            {
                "column": cell.get("column"),
                "column_index": cell.get("column_index"),
                "type": cell.get("type_key") or cell.get("type") or cell.get("display_name"),
                "masked_value": mask_value(str(cell.get("masked_value") or cell.get("value") or "")),
                "verdict": cell.get("decision", ""),
                "confidence": cell.get("confidence", 0.0),
                "column_support": cell.get("column_support", 0.0),
                "name_hint": cell.get("name_hint", 0.0),
            }
            for cell in _ranked_cells(record.get("cells", []) or [])
        ],
        "rule_basis": record.get("basis", ""),
        "needs_review": record.get("needs_review", False),
    }


def build_row_prompt(context: dict) -> str:
    instruction = (
        "당신은 개인정보 탐지 보조 분석기입니다. 주어진 Row의 마스킹된 Cell 근거만 근거로 "
        "이 행이 개인을 식별할 수 있는 개인 정보인지 판정하고 JSON만 반환하세요. "
        '응답 형식: {"row": int, "personality": str, "confidence": float, '
        '"reason": str, "requires_review": bool}'
    )
    return instruction + "\n\n" + json.dumps(context, ensure_ascii=False)


def build_code_group_prompt(group: dict) -> str:
    instruction = (
        "코드 그룹의 업무 의미와 개인정보 관련성을 JSON으로 반환하세요. "
        "필드: group, meaning, privacy_relevant, privacy_type, confidence"
    )
    return instruction + "\n\n" + json.dumps(group, ensure_ascii=False)
