"""공통코드(코드 그룹) 테이블 의미 분석.

GROUP_CODE | CODE | CODE_NAME 형태의 테이블에서 코드 그룹 자체의 의미,
개인정보 관련 코드 여부, 코드값 자체가 식별정보인지 여부를 판단한다.
규칙으로 판단이 불분명한 그룹은 Local LLM으로 보조 판정한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

GROUP_HINTS = ("GROUP", "TYPE", "CATEGORY", "CD_GRP", "DIV", "CLASS", "항", "그룹")
CODE_HINTS = ("CODE", "CD", "COD", "CODE_NO", "코드")
DESC_HINTS = ("NAME", "DESC", "LABEL", "DESCRIPTION", "명", "Name", "TEXT")

KEYWORD_MAP = {
    "GENDER": ("성별", "SEX", "GENDER", "男", "女", "남", "여"),
    "NATIONALITY": ("국적", "국", "NATION", "COUNTRY", " nationality"),
    "NAME": ("이름", "NAME", "姓名"),
    "PHONE": ("전화", "PHONE", "TEL", "번호"),
    "ADDRESS": ("주소", "ADDRESS", "ADDR"),
    "RESIDENT_ID": ("주민", "RESIDENT", " 주민"),
    "BLOOD": ("혈형", "BLOOD"),
    "MARITAL": ("결혼", "MARITAL"),
    "DIAGNOSIS": (" 진단", "질환", "DISEASE", "DIAGNOSIS"),
}

CODE_IS_IDENTIFIER_RE = re.compile(r"^\d{6,12}$|^[A-Z]{1,3}\d{5,10}$")


@dataclass
class CodeGroup:
    group_column: str
    code_column: str
    desc_column: str | None
    groups: list[str] = field(default_factory=list)
    distinct_codes: int = 0
    privacy_relevant: bool = False
    privacy_type: str = ""
    code_is_identifier: bool = False
    decision: str = "rule"
    confidence: float = 0.0
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "group_column": self.group_column,
            "code_column": self.code_column,
            "desc_column": self.desc_column,
            "groups": self.groups[:20],
            "distinct_codes": self.distinct_codes,
            "privacy_relevant": self.privacy_relevant,
            "privacy_type": self.privacy_type,
            "code_is_identifier": self.code_is_identifier,
            "decision": self.decision,
            "confidence": round(self.confidence, 3),
            "note": self.note,
        }


def _pick(columns: list[str], hints: tuple[str, ...]) -> str | None:
    for name in columns:
        upper = name.upper()
        for hint in hints:
            if hint.upper() in upper:
                return name
    return None


PROTECTED_SUFFIXES = ("_MASK", "_HASH", "_ENC", "_ENCRYPTED", "_SHA256", "_MD5")


def _pick_desc_column(header: list[str], code_col: str, group_col: str | None,
                      desc_cols: list[str], stats: dict) -> str | None:
    """Choose the description column belonging to this code column.

    설명 컬럼은 (1) 코드/그룹 컬럼과 같은 접두사를 가진 컬럼, (2) 코드 컬럼 바로
    다음 컬럼, (3) 나머지 descHint 컬럼 순으로 선택한다. 보호(MASK/HASH) 컬럼이나
    파일명/URL 같은 값만 담긴 컬럼을 설명으로하면 코드 그룹 판정이 원문 샘플을
    근거로 오하게 "개인정보 관련"로 flagged 될 수 있다.
    """
    candidates = [c for c in desc_cols if c not in (code_col, group_col)]
    if not candidates:
        return None

    def related(name: str) -> bool:
        for anchor in (code_col, group_col or ""):
            if not anchor:
                continue
            prefix = anchor.split("_")[0]
            if name.upper().startswith(prefix) or name.upper() == anchor.upper() + "_DESC":
                return True
        return False

    for name in candidates:
        if any(name.upper().endswith(s) for s in PROTECTED_SUFFIXES):
            continue
        if related(name):
            return name
    for name in candidates:
        if any(name.upper().endswith(s) for s in PROTECTED_SUFFIXES):
            continue
        idx = header.index(name)
        if idx == header.index(code_col) + 1:
            return name
    return candidates[0]


def analyze(header: list[str], sample_rows: list[list[str]], column_stats,
            privacy_columns: set[str] | None = None) -> list[CodeGroup]:
    """Detect code-group structure from header names plus bounded row samples."""
    stats = {column.name: column for column in column_stats}
    privacy_columns = privacy_columns or set()
    # "GROUP_DESC" 같은 컬럼은 그룹 키가 아니라 설명 컬럼이다.
    group_cols = [c for c in header if _pick([c], GROUP_HINTS) and not _pick([c], DESC_HINTS)]
    code_cols = [c for c in header if _pick([c], CODE_HINTS) and c not in group_cols]
    desc_cols = [c for c in header
                 if _pick([c], DESC_HINTS) and c not in group_cols + code_cols
                 and c not in privacy_columns]
    if not code_cols:
        return []

    groups: list[CodeGroup] = []
    for code_col in code_cols:
        code_stat = stats.get(code_col)
        if code_stat is None:
            continue
        distinct = code_stat.distinct.estimate()
        # 코드 컬럼은 반복되는 짧은 값이어야 한다.
        if distinct < 2 or distinct > 5000:
            continue
        group_col = None
        for candidate in group_cols:
            if candidate != code_col and stats[candidate].distinct.estimate() <= distinct:
                group_col = candidate
                break
        desc_col = _pick_desc_column(header, code_col, group_col, desc_cols, stats)

        group_values: set[str] = set()
        identifiers = 0
        descriptions: list[str] = []
        for row in sample_rows[:500]:
            if len(row) <= header.index(code_col):
                continue
            code = row[header.index(code_col)].strip()
            if CODE_IS_IDENTIFIER_RE.fullmatch(code):
                identifiers += 1
            if group_col is not None:
                gv = row[header.index(group_col)].strip()
                if gv:
                    group_values.add(gv.upper())
            if desc_col is not None:
                dv = row[header.index(desc_col)].strip()
                if dv and len(descriptions) < 30:
                    descriptions.append(dv)

        relevant_type = ""
        keywords: list[str] = []
        text = " ".join(group_values) + " " + " ".join(descriptions)
        for key, words in KEYWORD_MAP.items():
            if any(w.strip().upper() in text.upper() for w in words if w.strip()):
                relevant_type = key
                keywords.extend(w.strip() for w in words if w.strip().upper() in text.upper())
                break

        confidence = 0.0
        if group_col:
            confidence += 0.4
        if desc_col:
            confidence += 0.3
        if relevant_type:
            confidence += 0.3
        groups.append(
            CodeGroup(
                group_column=group_col or "",
                code_column=code_col,
                desc_column=desc_col,
                groups=sorted(group_values),
                distinct_codes=distinct,
                privacy_relevant=bool(relevant_type) or identifiers > len(sample_rows) * 0.3,
                privacy_type=relevant_type,
                code_is_identifier=identifiers > len(sample_rows) * 0.3,
                decision="rule" if confidence >= 0.5 else "pending",
                confidence=confidence,
                note=" ".join(keywords[:3]),
            )
        )
    return groups
