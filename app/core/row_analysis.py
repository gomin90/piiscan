"""Cell-level verdicts and row-level context analysis.

The rule engine decides **per cell**; the column's overall classification is only
supporting evidence. A column being "not a privacy column" never cancels a cell
that a detector confirmed, it only changes how confident that cell verdict is and
whether it is flagged for review.

Two-stage verdict: while a file streams, a column's support ratio is only a prefix
of the finished column, so using it during the scan would make the same cell
verdict depend on the order the rows happened to arrive in. Cells are therefore
recorded with their own evidence and the final, order-independent support ratio is
applied when the scan is complete.

Raw personal values are never stored here: every value reaching a record is the
output of `mask_value()`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Confidence at which a cell's own detector evidence is treated as settled.
CELL_CONFIRM_THRESHOLD = 0.70
CELL_REVIEW_THRESHOLD = 0.60
# A cell that only loosely resembles a pattern and has no supporting signal is
# not reported as a hit; it is counted in the diagnostics only.
IGNORE_VERDICT = "형식 유사(무시)"

# Row statuses. The three states are mutually exclusive and are what the UI and
# the exports report: confirmed evidence, plausible-but-unsettled evidence, none.
STATUS_CONFIRMED = "확정"
STATUS_POSSIBLE = "검토 필요"
STATUS_NONE = "없음"

# Types that identify a person on their own, without any other column.
DIRECT_IDENTIFIER_TYPES = {
    "RESIDENT_ID", "FOREIGNER_ID", "PASSPORT", "DRIVER_LICENSE", "CARD_NUMBER",
    "BANK_ACCOUNT", "SOCIAL_SECURITY", "INSURANCE_NUMBER", "BIOMETRIC", "IMSI", "IMEI",
    "SOCIAL_SECURITY_NUMBER",
}
# Types that identify a person only in combination with another attribute.
COMBINATORY_TYPES = {
    "NAME", "BIRTH_DATE", "ADDRESS", "PHONE_NUMBER", "EMAIL", "POSTCODE", "LOCATION",
    "UUID", "MAC", "IP", "SN", "CUSTOM_IDENTIFIER", "OTHER_PERSONAL",
}
# Pairs whose co-presence in the same row makes the row identifiable.
IDENTIFYING_PAIRS = (
    ({"NAME", "PHONE_NUMBER"}, "이름 + 전화번호"),
    ({"NAME", "EMAIL"}, "이름 + 이메일"),
    ({"NAME", "ADDRESS"}, "이름 + 상세주소"),
    ({"NAME", "BIRTH_DATE"}, "이름 + 생년월일"),
    ({"BIRTH_DATE", "ADDRESS"}, "생년월일 + 상세주소"),
    ({"EMAIL", "PHONE_NUMBER"}, "이메일 + 전화번호"),
    ({"NAME", "CARD_NUMBER"}, "이름 + 카드번호"),
    ({"NAME", "BANK_ACCOUNT"}, "이름 + 계좌번호"),
    ({"ADDRESS", "POSTCODE"}, " 상세주소 + 우편번호"),
    ({"BIRTH_DATE", "PHONE_NUMBER"}, "생년월일 + 전화번호"),
)

DIRECT_WEIGHT = 3.0
COMBINATION_WEIGHT = 2.0
REVIEW_WEIGHT = 0.5


@dataclass
class CellRecord:
    """One detector hit on one cell."""

    row: int
    column_index: int
    column: str
    type_key: str
    display_name: str
    confidence: float
    masked_value: str
    evidence: str
    confirmed: bool
    masked: bool
    sensitivity: float
    priority: int
    decision: str = ""
    needs_review: bool = False
    column_support: float = 0.0

    def as_dict(self) -> dict:
        return {
            "row": self.row,
            "column_index": self.column_index,
            "column": self.column,
            "type": self.type_key,
            "display_name": self.display_name,
            "confidence": round(self.confidence, 3),
            "value": self.masked_value,
            "evidence": self.evidence,
            "confirmed": self.confirmed,
            "masked": self.masked,
            "decision": self.decision,
            "needs_review": self.needs_review,
            "column_support": round(self.column_support, 4),
        }

    @classmethod
    def from_row(cls, raw: dict) -> "CellRecord":
        return cls(
            row=raw["row"], column_index=raw["column_index"], column=raw["column"],
            type_key=raw["type_key"], display_name=raw["display_name"],
            confidence=raw["confidence"], masked_value=raw["masked_value"],
            evidence=raw["evidence"], confirmed=raw["confirmed"], masked=raw["masked"],
            sensitivity=raw["sensitivity"], priority=raw["priority"],
            decision=raw["decision"], needs_review=raw["needs_review"],
            column_support=raw["column_support"],
        )


def cell_decision(confidence: float, confirmed: bool, column_support: float = 0.0,
                  name_hint: float = 0.0) -> tuple[str, float, bool]:
    """Decide a single cell from its own evidence; column statistics only adjust confidence.

    Returns (verdict, confidence, needs_review). Three verdicts are possible:
    ``개인정보`` (confirmed and confident), ``검토 필요`` (plausible but not
    settled), ``형식 유사(무시)`` (only a loose pattern match with no support).
    ``column_support`` must be the ratio over the **whole** column, not a prefix,
    otherwise the verdict would depend on row order.
    """
    if column_support >= 0.8:
        confidence += 0.06
    elif column_support < 0.2 and not confirmed:
        confidence -= 0.10
    if name_hint:
        confidence += 0.02 * min(name_hint, 0.8)
    confidence = max(0.0, min(confidence, 1.0))
    if confirmed and confidence >= CELL_CONFIRM_THRESHOLD:
        return "개인정보", confidence, False
    if confidence >= CELL_REVIEW_THRESHOLD and (confirmed or column_support >= 0.3 or name_hint >= 0.35):
        return "검토 필요", confidence, True
    if column_support >= 0.5 and confidence >= 0.45:
        return "검토 필요", confidence, True
    return IGNORE_VERDICT, confidence, False


def analyse_row(cells: list[CellRecord]) -> dict:
    """Combine the cell verdicts of one row into a row-level verdict."""
    confirmed_types = {cell.type_key for cell in cells if cell.decision == "개인정보"}
    review_types = {cell.type_key for cell in cells if cell.needs_review}

    basis: list[str] = []
    direct = sorted(confirmed_types & DIRECT_IDENTIFIER_TYPES)
    if direct:
        basis.append("직접 식별 정보: " + ", ".join(direct))

    combinations = []
    for pair, label in IDENTIFYING_PAIRS:
        if pair <= confirmed_types:
            combinations.append(label)
    if combinations:
        basis.append("Row 조합: " + "; ".join(sorted(set(combinations))))
    if len(confirmed_types) >= 3:
        basis.append(f"다중 유형 조합 ({len(confirmed_types)}개 유형이 같은 Row)")
    if not confirmed_types and review_types:
        basis.append("Cell 형식만 일치 -> Row 문맥으로 확인 필요")
    elif not basis and confirmed_types:
        basis.append("Cell evidence: " + ", ".join(sorted(confirmed_types)))

    score = 0.0
    score += DIRECT_WEIGHT * len(direct)
    score += COMBINATION_WEIGHT * len(set(combinations))
    score += 0.5 * len(confirmed_types - DIRECT_IDENTIFIER_TYPES
                       - {t for pair, _ in IDENTIFYING_PAIRS for t in pair if t in confirmed_types})
    score += REVIEW_WEIGHT * len(review_types)

    if score >= 5.0:
        level = "매우 높음"
    elif score >= 3.0:
        level = "높음"
    elif score >= 1.5:
        level = "중간"
    elif review_types:
        level = "검토 필요"
    else:
        level = "낮음"

    if confirmed_types:
        status = STATUS_CONFIRMED
    elif review_types:
        status = STATUS_POSSIBLE
    else:
        status = STATUS_NONE

    return {
        "has_pii": bool(confirmed_types or review_types),
        "status": status,
        "confirmed_pii": sorted(confirmed_types),
        "possible_pii": sorted(review_types),
        "pii_types": sorted(confirmed_types),
        "review_types": sorted(review_types),
        "needs_review": bool(review_types),
        "basis": " | ".join(basis) or "개인정보 Cell 없음",
        "row_score": round(score, 2),
        "risk_level": level,
    }


@dataclass
class RowRecord:
    """Row-level result: whether the record contains personal data and where."""

    row: int
    has_pii: bool
    cells: list[CellRecord] = field(default_factory=list)
    pii_types: list[str] = field(default_factory=list)
    review_types: list[str] = field(default_factory=list)
    confirmed_pii: list[str] = field(default_factory=list)
    possible_pii: list[str] = field(default_factory=list)
    status: str = STATUS_NONE
    column_names: list[str] = field(default_factory=list)
    column_indexes: list[int] = field(default_factory=list)
    basis: str = ""
    confidence: float = 0.0
    needs_review: bool = False
    risk_level: str = "낮음"
    cells_checked: int = 0
    cell_count: int = 0
    ai_review: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "row": self.row,
            "has_pii": self.has_pii,
            "status": self.status,
            "confirmed_pii": self.confirmed_pii,
            "possible_pii": self.possible_pii,
            "pii_types": self.pii_types,
            "review_types": self.review_types,
            "columns": self.column_names,
            "column_indexes": self.column_indexes,
            "cells": [cell.as_dict() for cell in self.cells],
            "cell_count": self.cell_count,
            "basis": self.basis,
            "confidence": round(self.confidence, 3),
            "needs_review": self.needs_review,
            "risk_level": self.risk_level,
            "cells_checked": self.cells_checked,
            "ai_review": self.ai_review,
        }

    @classmethod
    def from_row(cls, raw: dict) -> "RowRecord":
        return cls(
            row=raw["row"], has_pii=raw["has_pii"], status=raw["status"],
            confirmed_pii=raw["confirmed_pii"], possible_pii=raw["possible_pii"],
            pii_types=raw["confirmed_pii"], review_types=raw["possible_pii"],
            column_names=raw["column_names"], column_indexes=raw["column_indexes"],
            basis=raw["basis"], confidence=raw["confidence"],
            needs_review=raw["needs_review"], risk_level=raw["risk_level"],
            cells_checked=raw["cells_checked"], cell_count=raw["cell_count"],
            ai_review=raw["ai_review"],
        )
