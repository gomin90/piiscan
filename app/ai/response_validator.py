"""LLM 응답의 JSON 스키마 검증 및 normalization."""
from __future__ import annotations

import json
import re

from app.ai.prompt_manager import VALID_TYPES

JSON_BLOCK_RE = re.compile(r"\{[\s\S]*?\}")


def parse_response(text: str) -> dict | None:
    """Extract the first JSON object from a model response."""
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = JSON_BLOCK_RE.search(text)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    return data


def validate(data: dict, column: str) -> dict | None:
    """Return a normalised judgement or None when the response is unusable."""
    if not isinstance(data, dict):
        return None
    privacy_type = str(data.get("privacy_type", "")).strip().upper()
    if privacy_type not in VALID_TYPES:
        return None
    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        return None
    if not 0.0 <= confidence <= 1.0:
        return None
    reason = str(data.get("reason", "")).strip()
    if len(reason) > 400:
        reason = reason[:400]
    if len(reason) > 120 and any(ch.isdigit() for ch in reason) and re.search(r"\d{6,}", reason):
        reason = "통계 기반 의미 판단 ( 긴 숫자 포함 근거 제외 )"
    return {
        "column": str(data.get("column", column)),
        "privacy_type": privacy_type,
        "confidence": round(confidence, 3),
        "reason": reason,
        "requires_review": bool(data.get("requires_review", False)),
    }
