"""이름 detector: 한글/외국인 이름 패턴 + 컬럼 의미."""
from __future__ import annotations

import re

from app.detectors.address import REGION_ONLY
from app.detectors.base import Detector, Match, ValueProfile

KOREAN_SURNAMES = {
    "김", "이", "박", "정", "홍", "최", "조", "윤", "구", "백", "신", "나", "한", "장", "강",
    "임", "황", "권", "오", "안", "유", "모", "민", "최", "석", "전", "설", "추", "라", "마",
}
MASKED_NAME = re.compile(r"^[가-힣]{1,2}[*＊Xx]{1,3}$|^[A-Z][A-Za-z]{0,3}[*＊Xx]{2,6}$")
LATIN_NAME = re.compile(r"^[A-Z][a-z]{1,12}([ ,.][A-Z][a-z]{1,12}){1,3}$")
# Organisations are not people: department/branch/organisation tokens end in a
# fixed suffix and must never be read as a person's name.
ORG_SUFFIXES = (
    "팀", "부", "部", "组", "_group", "section", "dept", "department", "division",
    "지점", "지사", "센터", "사무국", "사업부", "課", "室",
)
ORG_HINTS = ("DEPT", "DEPARTMENT", "ORG", "DIVISION", "TEAM", "BRANCH", "SECTION", "GROUP", "부", "팀")


class NameDetector(Detector):
    type_key = "NAME"
    display_name = "이름"
    priority = 1
    sensitivity = 0.7
    candidate_tags = frozenset({"hangul", "latin", "masked"})
    name_hints = ("NAME", "이름", "CUSTOMER", "PATIENT", "회원", "USER_NAME", "_full_name")

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        v = value.strip()
        if not v:
            return None
        upper_column = column_name.upper()
        if any(hint.upper() in upper_column for hint in ORG_HINTS):
            return None
        if v.endswith(ORG_SUFFIXES) or v.upper().endswith(ORG_SUFFIXES):
            return None
        if MASKED_NAME.match(v):
            return Match(self.type_key, self.display_name, 0.8, masked=True,
                        evidence="마스킹된 이름", priority=self.priority, sensitivity=self.sensitivity)
        if profile.hangul and profile.length <= 4 and profile.digits == 0 and profile.other == 0:
            first = v[:1]
            if first in KOREAN_SURNAMES and 2 <= profile.length <= 4:
                return Match(self.type_key, self.display_name, 0.9,
                             evidence="한글 surname + 이름 길이/분포",
                             priority=self.priority, sensitivity=self.sensitivity)
            if profile.length >= 2 and v in REGION_ONLY:
                return None  # region / city token, not a person name
            return None
        if LATIN_NAME.match(v):
            return Match(self.type_key, self.display_name, 0.8,
                        evidence="이름+성 형식", priority=self.priority, sensitivity=self.sensitivity)
        return None
