"""외국인등록번호 detector."""
from __future__ import annotations

import re

from app.detectors.base import Detector, Match, ValueProfile
from app.utils.validators import birth_part_valid, digits_only

FULL_RE = re.compile(r"^\d{2}[A-Z]\d{7}$|^\d{12,13}$")
MASKED_RE = re.compile(r"^\d{6}[-.][*＊]{5,6}$")


class ForeignerIdDetector(Detector):
    type_key = "FOREIGNER_ID"
    display_name = "외국인등록번호"
    priority = 1
    sensitivity = 0.9
    candidate_tags = frozenset({"digits", "digits_sep", "latin", "masked", "alnum"})
    name_hints = ("외국인", "FOREIGN", "ALIEN", "foreigner", "IATI", "FID")

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        v = value.strip().upper()
        if MASKED_RE.match(v):
            return Match(self.type_key, self.display_name, 0.7, masked=True,
                        evidence="외국인등록번호 마스킹 패턴", priority=self.priority, sensitivity=self.sensitivity)
        digits = digits_only(v)
        if len(digits) == 12 and birth_part_valid(digits[:6]):
            # 12자 숫자 alone is ambiguous (구형 주민번호/보험번호 also fit); the
            # column name decides how confidently we report it.
            hinted = self.column_affinity(column_name) >= 0.35
            return Match(self.type_key, self.display_name, 0.85 if hinted else 0.45,
                        evidence="12자 외국인등록번호 형식" if hinted
                        else "12자 숫자 - 외국인등록번호 후보 (원천 확인 필요)",
                        priority=self.priority, sensitivity=self.sensitivity)
        if FULL_RE.match(v) and len(v) == 10:
            # Documented alternative format: 2 digits + 1 letter + 7 digits.
            hinted = self.column_affinity(column_name) >= 0.40
            return Match(self.type_key, self.display_name, 0.8 if hinted else 0.6,
                         evidence="외국인등록번호 대체 형식 (숫자2+알파1+숫자7)"
                                  if hinted else "외국인등록번호 Similar 형식 (원천 확인 필요)",
                         priority=self.priority, sensitivity=self.sensitivity,
                         confirmed=hinted)
        return None
