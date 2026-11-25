"""위험도 및 우선순위 평가."""
from __future__ import annotations

from app.config.privacy_policy import get_policy
from app.utils.masking import looks_masked

RISK_LEVELS = ["매우 높음", "높음", "보통", "낮음", "확인 필요"]
COMPOUND_THRESHOLD = 3  # 같은 테이블 내 여러 개인정보 유형


def level_from_score(score: float) -> str:
    if score >= 0.85:
        return "매우 높음"
    if score >= 0.65:
        return "높음"
    if score >= 0.45:
        return "보통"
    if score >= 0.25:
        return "낮음"
    return "확인 필요"


def assess_column(finding: dict, protection: dict | None, ai: dict | None, table_pii_types: int) -> dict:
    """Combine type sensitivity, detection ratio, protection state and confidence."""
    types = finding.get("types", [])
    if not types:
        score = 0.0
        drivers = ["규칙 기반 탐지 없음"]
    else:
        sensitivity = max(t["sensitivity"] for t in types)
        ratio = max(t["ratio"] for t in types)
        confidence = max(t["rule_confidence"] for t in types)
        score = sensitivity * (0.45 + 0.55 * min(ratio, 1.0)) * confidence
        drivers = [f"민감도 {sensitivity:.2f}", f"탐지 비율 {ratio:.2f}", f"규칙 신뢰도 {confidence:.2f}"]

        if len(types) > 1:
            score += 0.08
            drivers.append(f"복합 유형 {len(types)}개")

        if protection:
            if protection.get("status_score", 0) >= 0.8:
                score -= 0.30
                drivers.append("보호 처리 확인 (MASK/HASH/ENC 검증)")
            elif protection.get("status_score", 0) >= 0.4:
                score -= 0.12
                drivers.append("부분 보호 처리")
            else:
                score += 0.05
                drivers.append("보호 컬럼 존재하나 값 검증 실패")
        else:
            drivers.append("보호 컬럼 없음")

        if table_pii_types >= COMPOUND_THRESHOLD:
            score += 0.12
            drivers.append(f"동일 테이블 내 {table_pii_types}개 개인정보 유형 (복합 식별)")

    masked_ratio = finding.get("masked_like", 0) / max(finding.get("non_empty", 1), 1)
    if masked_ratio >= 0.6 and types:
        score -= 0.10
        drivers.append("값 마스킹 적용")

    if ai:
        if ai.get("requires_review"):
            score = max(score, 0.20)
            drivers.append("AI 판단 불확실 -> 확인 필요")
        elif ai.get("privacy_type") != "NONE":
            score += 0.06 * ai.get("confidence", 0.0)
            drivers.append(f"AI 보조 판단 ({ai['privacy_type']})")

    if not types and (finding.get("masked_like", 0) == 0):
        level = "확인 필요"
    else:
        level = level_from_score(max(0.0, min(score, 1.0)))

    # Spec section 7: a custom identifier that is confirmed to be personal data
    # moves the *risk level* up one step. The numeric score and the rule
    # confidence are deliberately left alone - a step up is a judgement about
    # consequence, not about how certain the match is. Tier is a separate axis
    # (legal priority) and is reported alongside, never merged into the level.
    policy = get_policy()
    step_up = [t["type_key"] for t in types if policy.risk_step_up(t["type_key"])]
    if step_up:
        index = RISK_LEVELS.index(level)
        if index > 0:
            level = RISK_LEVELS[index - 1]
            drivers.append(
                "Custom Identifier 개인정보 확인 -> 위험 단계 1 상향"
                f" (근거: {'/'.join(step_up)})"
            )

    tiers = {policy.tier(t["type_key"]) for t in types}
    return {
        "score": round(max(0.0, min(score, 1.0)), 3),
        "level": level,
        "tier": min(tiers) if tiers else 2,
        "drivers": drivers,
    }
