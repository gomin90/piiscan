"""외국인등록번호 detector."""
from __future__ import annotations

import re

from app.detectors.base import Detector, Match, ValueProfile
from app.utils.validators import birth_part_valid, digits_only, resident_checksum

FULL_RE = re.compile(r"^\d{2}[A-Z]\d{7}$|^\d{12,13}$")
MASKED_RE = re.compile(r"^\d{6}[-.][*＊]{5,6}$")


class ForeignerIdDetector(Detector):
    type_key = "FOREIGNER_ID"
    display_name = "외국인등록번호"
    priority = 1
    sensitivity = 0.9
    candidate_tags = frozenset({"digits", "digits_sep", "latin", "masked"})
    name_hints = ("외국인", "FOREIGN", "ALIEN", "foreigner", "IATI", "FID")

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        v = value.strip().upper()
        if MASKED_RE.match(v):
            return Match(self.type_key, self.display_name, 0.7, masked=True,
                        evidence="외국인등록번호 마스킹 패턴", priority=self.priority, sensitivity=self.sensitivity)
        digits = digits_only(v)
        if len(digits) in (12, 13) and birth_part_valid(digits[:6]):
            valid = resident_checksum(digits)
            return Match(self.type_key, self.display_name, 0.9 if valid else 0.6,
                        evidence="12자리 외국인등록번호 형식", priority=self.priority, sensitivity=self.sensitivity)
        if len(v) == 10 and v[:2].isdigit() and v[2].isalpha() and v[3:].isdigit():
            return Match(self.type_key, self.display_name, 0.65,
                        evidence="외국인등록번호 대체 형식", priority=self.priority, sensitivity=self.sensitivity)
        return None
