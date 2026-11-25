"""이름 detector: person names, and the product / organisation look-alikes that
must not be read as names.

Section 3.3 of the specification requires the detector to tell apart

  GOODS_GROUP(KEY) = "오디오"      NAME = "비스포크 오디오"

i.e. a value is a *person* name only when the column meaning, the sibling columns
of the table and the shape of the value all agree. Otherwise the cell becomes a
candidate (`검토 필요`), never a confirmed name. The alias and hint lists come from
`resources/rules/privacy_kr_2026.json` so they can be edited without code changes.
"""
from __future__ import annotations

import re

from app.config.privacy_policy import get_policy
from app.detectors.address import REGION_ONLY
from app.detectors.base import Detector, Match, ScanContext, ValueProfile

KOREAN_SURNAMES = {
    "김", "이", "박", "정", "홍", "최", "조", "윤", "구", "백", "신", "나", "한", "장", "강",
    "임", "황", "권", "오", "안", "유", "모", "민", "석", "전", "설", "추", "라", "마",
}
MASKED_NAME = re.compile(r"^[가-힣]{1,2}[*＊Xx]{1,3}$|^[A-Z][A-Za-z]{0,3}[*＊Xx]{2,6}$")
LATIN_NAME = re.compile(r"^[A-Z][a-z]{1,12}([ ,.][A-Z][a-z]{1,12}){1,3}$")
#: A Korean full name is a surname + given name with no spaces.
KOREAN_FULL = re.compile(r"^[가-힣]{2,5}$")
ORG_SUFFIXES = (
    "팀", "부", "部", "组", "_group", "section", "dept", "department", "division",
    "지점", "지사", "센터", "사무국", "사업부", "課", "室",
)
ORG_HINTS = ("DEPT", "DEPARTMENT", "ORG", "DIVISION", "TEAM", "BRANCH", "SECTION", "GROUP", "부", "팀")
#: Words that mark a value as a product / service rather than a person.
PRODUCT_WORDS = (
    "오디오", "tv", "phone", "스마트", "노트북", "tablet", "model", "series", "edition",
    "package", "서비스", "상품", "제품", "그룹", "category", "group",
)


class NameDetector(Detector):
    type_key = "NAME"
    display_name = "이름"
    priority = 1
    sensitivity = 0.7
    candidate_tags = frozenset({"hangul", "latin", "masked"})
    name_hints = ("NAME", "이름", "CUSTOMER", "PATIENT", "회원", "USER_NAME", "_full_name")

    def __init__(self, policy=None):
        self.policy = policy or get_policy()
        definition = self.policy.get("NAME")
        if definition:
            self.name_hints = definition.column_aliases or self.name_hints
            self.negative_hints = definition.negative_hints
            self.priority = definition.tier
            self.sensitivity = definition.sensitivity

    def column_affinity(self, column_name: str) -> float:
        return self.policy.column_affinity("NAME", column_name)

    def _commercial_context(self, value: str, column_name: str, context: ScanContext | None) -> bool:
        """True when the column/table (or a multi-word value) points at products."""
        lowered = (value or "").lower().strip()
        if not lowered:
            return False
        haystack = (column_name + " " + (context.table_name if context else "")).lower()
        for hint in self.negative_hints:
            if hint.lower() in haystack:
                return True
        if any(word in lowered for word in PRODUCT_WORDS):
            return True
        # Sibling columns only matter for a value that is not already a clean
        # single-token name: "비스포크 오디오" beside a GOODS_GROUP column is a
        # product line, "김민준" beside one is still a person.
        if context and " " in lowered:
            for name in context.column_names:
                if name == column_name:
                    continue
                if any(hint.lower() in name.lower() for hint in self.negative_hints):
                    return True
        return False

    def match(self, value: str, profile: ValueProfile, column_name: str = "",
              context: ScanContext | None = None) -> Match | None:
        v = value.strip()
        if not v:
            return None
        upper_column = column_name.upper()
        if any(hint.upper() in upper_column for hint in ORG_HINTS):
            return None
        if v.endswith(ORG_SUFFIXES) or v.upper().endswith(ORG_SUFFIXES):
            return None
        affinity = self.column_affinity(column_name)
        commercial = self._commercial_context(v, column_name, context)

        if MASKED_NAME.match(v):
            # A masked name is still personal data, but a masked product code is
            # not: trust the mask only when the column really means "name".
            if affinity > 0.0 and not commercial:
                return Match(self.type_key, self.display_name, 0.8, masked=True,
                             evidence="마스킹된 이름", priority=self.priority,
                             sensitivity=self.sensitivity)
            return None

        if profile.hangul and profile.digits == 0:
            if KOREAN_FULL.fullmatch(v) and v[:1] in KOREAN_SURNAMES and 2 <= profile.length <= 5:
                if commercial:
                    return Match(self.type_key, self.display_name, 0.55, confirmed=False,
                                 evidence="이름 형식이지만 제품/그룹 문맥 -> 후보",
                                 priority=self.priority, sensitivity=self.sensitivity)
                return Match(self.type_key, self.display_name,
                             0.9 if affinity > 0.0 else 0.75,
                             evidence="한글 surname + 이름 길이/분포",
                             priority=self.priority, sensitivity=self.sensitivity)
            if v in REGION_ONLY:
                return None  # region / city token, not a person name
            if affinity > 0.0 and profile.length >= 4:
                # "비스포크 오디오" in a NAME column: a product line is at least as
                # likely as a person, so this stays a candidate.
                return Match(self.type_key, self.display_name, 0.5, confirmed=False,
                             evidence="NAME 컬럼 값이지만 - person 이름 확정 불가 -> 후보",
                             priority=self.priority, sensitivity=self.sensitivity)
            return None

        if LATIN_NAME.match(v):
            if commercial:
                return Match(self.type_key, self.display_name, 0.5, confirmed=False,
                             evidence="이름 형식이지만 제품/브랜드 문맥 -> 후보",
                             priority=self.priority, sensitivity=self.sensitivity)
            return Match(self.type_key, self.display_name, 0.8 if affinity > 0 else 0.7,
                         evidence="이름+성 형식", priority=self.priority, sensitivity=self.sensitivity)

        if affinity >= 0.4 and re.fullmatch(r"[A-Za-z]{2,20}", v):
            return Match(self.type_key, self.display_name, 0.6,
                         evidence="NAME 컬럼의 단일 identifier 값",
                         priority=self.priority, sensitivity=self.sensitivity)
        return None
