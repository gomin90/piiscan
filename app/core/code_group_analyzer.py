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


def analyze(header: list[str], sample_rows: list[list[str]], column_stats) -> list[CodeGroup]:
    """Detect code-group structure from header names plus bounded row samples."""
    stats = {column.name: column for column in column_stats}
    group_cols = [c for c in header if _pick([c], GROUP_HINTS)]
    code_cols = [c for c in header if _pick([c], CODE_HINTS) and c not in group_cols]
    desc_cols = [c for c in header if _pick([c], DESC_HINTS) and c not in group_cols + code_cols]
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
        desc_col = next((c for c in desc_cols if c not in (code_col, group_col)), None)

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
