"""Per-cell verdict model.

The scanner's unit of judgement is the **cell**, not the column. Every cell of
every row therefore gets one verdict, in exactly one of six states:

状态              Meaning
``개인정보``     A rule (or the model) confirmed personal data in this cell.
``검토 필요``    Evidence is plausible but not settled.
``비개인정보``  The rules positively exclude personal data (a code, an amount,
                a boolean, a hash, a system timestamp, ...).
``미평가``       Nothing matched and no model judgement was made: the cell is
                *not* cleared, only unassessed.
``빈 셀``        The cell contained no value.

``미평가`` is deliberately distinct from ``비개인정보``: absence of a match is
not proof of absence of personal data. Treating the two as the same would let a
wide file look clean when it was simply never judged.
"""
from __future__ import annotations

import re

from app.utils.masking import mask_value

STATUS_PII = "개인정보"
STATUS_REVIEW = "검토 필요"
STATUS_NON = "비개인정보"
STATUS_UNRATED = "미평가"
STATUS_EMPTY = "빈 셀"

# How conclusive a verdict is, used when several detectors speak about one cell:
# a confirmed match outranks "needs review", which outranks a plain clearance.
STATUS_RANK = {STATUS_PII: 3, STATUS_REVIEW: 2, STATUS_NON: 1, STATUS_UNRATED: 0, STATUS_EMPTY: 0}

# Formats that are, by shape alone, not personal data. Each entry is
# (label, pattern) and is only consulted *after* every privacy detector has
# already run on the value, so these never override a real detection.
NON_PERSONAL_PATTERNS = (
    ("Boolean 값", re.compile(r"^(y|n|yes|no|true|false|t|f|예|아니요)$", re.I)),
    ("순번/정수 값", re.compile(r"^\d{1,7}$")),
    ("금액/비율", re.compile(r"^-?\d{1,9}[.,]\d{1,4}$")),
    ("백분율", re.compile(r"^-?\d{1,4}(\.\d{1,2})?%$")),
    ("단위 값", re.compile(r"^-?\d{1,6}(\.\d{1,3})?(kg|g|ml|km|m|mm|분|초|시간)$", re.I)),
    ("소수 코드", re.compile(r"^\d{1,3}[.]\d{1,6}$")),
    ("분류 코드", re.compile(r"^[A-Z]{1,4}[_-]?\d{1,6}$")),
    ("버전 번호", re.compile(r"^\d+(\.\d+){1,3}$")),
    ("해시 값", re.compile(r"^[0-9a-fA-F]{32,128}$")),
    ("Base64 blob", re.compile(r"^[A-Za-z0-9+/]{24,}={0,2}$")),
    ("일반 URL", re.compile(r"^https?://[^\s]+$", re.I)),
    ("색상 값", re.compile(r"^#[0-9a-fA-F]{6}$")),
    ("비어 있지 않은 구분자", re.compile(r"^[-_.]{1,3}$")),
)

# Column names that describe a system/business field rather than a person.
CODE_NAME_TOKENS = ("no", "id", "cd", "code", "seq", "num", "key", "ref", "flag",
                    "status", "type", "category", "qty", "count", "amount", "price",
                    "version", "url", "hash", "checksum", "color", "state")


def rule_negative_verdict(value: str, column_name: str) -> tuple[str, str] | None:
    """Return (label, evidence) when the rules can affirm the cell is not personal.

    Only unambiguous shapes are cleared. Free text, names, addresses and mixed
    natural language are never cleared by the rule engine - they stay ``미평가``
    so the model, or a human, can still judge them.
    """
    text = (value or "").strip()
    if not text:
        return None
    for label, pattern in NON_PERSONAL_PATTERNS:
        if pattern.match(text):
            return label, f"규칙: {mask_value(text[:24])!r} -> {label.strip()}"
    lowered = column_name.lower()
    if any(token in lowered for token in CODE_NAME_TOKENS):
        if re.match(r"^[A-Za-z0-9_\-\./]{1,32}$", text) and not re.search(r"[가-힣]", text):
            return "시스템 코드", f"규칙: {column_name} 컬럼의 코드 값 {mask_value(text[:24])!r}"
    return None

# ---------------------------------------------------------------------------
# KEY selection
# ---------------------------------------------------------------------------
STATUS_KEY = "KEY 제외"

STATUSES = (STATUS_PII, STATUS_REVIEW, STATUS_NON, STATUS_UNRATED, STATUS_EMPTY, STATUS_KEY)

# Storage codes for the compact row ledger (one byte per cell, see detail_store).
CODE_EMPTY = 0
CODE_KEY = 1
CODE_DETAIL = 2          # the cell has a `cell_info` record that carries its verdict
CODE_CLEARED_BASE = 3    # 3 + index of the clearing rule (see NON_PERSONAL_LABELS)
NON_PERSONAL_LABELS = tuple(label for label, _ in NON_PERSONAL_PATTERNS) + ("시스템 코드",)
CLEARED_LABELS = NON_PERSONAL_LABELS


def status_to_code(status: str, rule_verdict: str = "") -> int:
    """Map a status onto the single storage byte for that cell."""
    if status == STATUS_EMPTY:
        return CODE_EMPTY
    if status == STATUS_KEY:
        return CODE_KEY
    if status == STATUS_NON:
        try:
            return CODE_CLEARED_BASE + NON_PERSONAL_LABELS.index(rule_verdict)
        except ValueError:
            return CODE_CLEARED_BASE
    return CODE_DETAIL


def code_to_status(code: int) -> str:
    if code == CODE_EMPTY:
        return STATUS_EMPTY
    if code == CODE_KEY:
        return STATUS_KEY
    if code == CODE_DETAIL:
        return STATUS_UNRATED
    if code >= CODE_CLEARED_BASE:
        return STATUS_NON
    return STATUS_UNRATED


def code_to_evidence(code: int) -> str:
    """Explain a derived cell without storing its value (space + privacy)."""
    if code == CODE_EMPTY:
        return "규칙: 빈 셀"
    if code == CODE_KEY:
        return "규칙: 사용자가 지정한 KEY 컬럼 - 분석 제외"
    if code >= CODE_CLEARED_BASE:
        index = code - CODE_CLEARED_BASE
        label = NON_PERSONAL_LABELS[index] if index < len(NON_PERSONAL_LABELS) else "비개인정보 형식"
        return f"규칙: {label} 형식 (원문 보존되지 않음)"
    return "규칙: 판정 상세 없음"


def code_to_rule_verdict(code: int) -> str:
    """Short rule label for a derived cell (the value itself is not stored)."""
    if code == CODE_EMPTY:
        return "빈 셀"
    if code == CODE_KEY:
        return "KEY 컬럼(분석 제외)"
    if code >= CODE_CLEARED_BASE:
        index = code - CODE_CLEARED_BASE
        return NON_PERSONAL_LABELS[index] if index < len(NON_PERSONAL_LABELS) else "비개인정보 형식"
    return ""


def normalise_value(value: str) -> str:
    """ canonical form used for fingerprinting identical personal-data values.

    Only the shape is normalised (case, surrounding whitespace, separators that
    carry no meaning) so that the same value written two ways groups together.
    """
    text = (value or "").strip()
    if not text:
        return ""
    text = text.lower()
    text = re.sub(r"[\s\-_./]+", "", text)
    return text
