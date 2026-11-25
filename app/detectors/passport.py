"""여권번호 detector: 국가별 형식과 마스킹 패턴."""
from __future__ import annotations

import re

from app.detectors.base import Detector, Match, ValueProfile
from app.utils.validators import digits_only

KR_RE = re.compile(r"^[EDE][0-9]{7,8}$")
GENERIC_RE = re.compile(r"^[A-Z]{1,2}[0-9]{6,9}$")
MASKED_RE = re.compile(r"^[A-Z]{1,2}\d{0,6}[*＊Xx]{1,8}\d{0,3}$")
US_RE = re.compile(r"^\d{8,9}$")


class PassportDetector(Detector):
    type_key = "PASSPORT"
    display_name = "여권번호"
    priority = 1
    sensitivity = 0.9
    candidate_tags = frozenset({"latin", "digits", "digits_sep", "alnum", "masked", "dot_token"})
    name_hints = ("여권", "PASSPORT", "PASPORT", "TRAVEL")
    # Columns named like an internal record key are not passports just because
    # the value happens to look like one (C1234567, E9876543).
    negative_hints = ("CUST", "CUSTOMER", "EMP", "ORDER", "SEQ", "ACCOUNT", "PROD", "DEPT", "MEMBER")

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        v = value.strip().upper()
        if not v or len(v) > 12:
            return None
        if self.name_excludes(column_name):
            if KR_RE.match(v) or GENERIC_RE.match(v):
                return Match(self.type_key, self.display_name, 0.45, confirmed=False,
                             evidence=f"{column_name} 값이 여권번호 형식과 유사 (내부 ID 가능성 -> 검토 필요)",
                             priority=self.priority, sensitivity=self.sensitivity)
            return None
        if MASKED_RE.match(v) and len(digits_only(v)) >= 3:
            return Match(self.type_key, self.display_name, 0.7, masked=True,
                        evidence="여권번호 마스킹 패턴", priority=self.priority, sensitivity=self.sensitivity)
        if KR_RE.match(v):
            return Match(self.type_key, self.display_name, 0.9,
                        evidence="한국 여권번호 형식", priority=self.priority, sensitivity=self.sensitivity)
        if GENERIC_RE.match(v) and len(digits_only(v)) >= 6:
            # A generic letter+digits run matches many internal record keys. It is
            # only evidence of a passport when the column meaning is unambiguous
            # (spec §3: PASSPORT is otherwise an AI-review case).
            hinted = self.column_affinity(column_name) >= 0.60
            return Match(self.type_key, self.display_name, 0.75 if hinted else 0.6,
                         evidence="국가별 여권번호 형식" if hinted
                         else "여권번호와 유사한 형식 (원천 확인 필요)",
                         priority=self.priority, sensitivity=self.sensitivity,
                         confirmed=hinted)
        if "PASSPORT" in v:
            return Match(self.type_key, self.display_name, 0.6,
                        evidence="값에 PASSPORT 표기", priority=self.priority, sensitivity=self.sensitivity)
        return None
