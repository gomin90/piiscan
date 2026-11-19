"""생년월일 detector."""
from __future__ import annotations

import re

from app.detectors.base import Detector, Match, ValueProfile
from app.utils.validators import plausible_date

ISO_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T]\d{1,2}:\d{2}(:\d{2})?)?$")
COMPACT_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
SHORT_RE = re.compile(r"^(\d{2})(\d{2})(\d{2})$")
MASKED_RE = re.compile(r"^\d{4}[-/]\d{2}[-/][*＊]{2}$|^\d{2}[-/][*＊]{2}[-/]\d{2}$")


class BirthDateDetector(Detector):
    type_key = "BIRTH_DATE"
    display_name = "생년월일"
    priority = 1
    sensitivity = 0.7
    candidate_tags = frozenset({"digits", "digits_sep"})
    name_hints = ("BIRTH", "DOB", "생년", "출생", "BIRTHDAY", "YEARS")

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        v = value.strip().replace("/", "-")
        if MASKED_RE.match(v):
            return Match(self.type_key, self.display_name, 0.65, masked=True,
                        evidence="마스킹된 생년월일", priority=self.priority, sensitivity=self.sensitivity)
        m = ISO_RE.match(v) or COMPACT_RE.match(v)
        if m:
            year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if plausible_date(year, month, day):
                return Match(self.type_key, self.display_name, 0.9,
                            evidence="ISO/8자리 생년월일", priority=self.priority, sensitivity=self.sensitivity)
        m = SHORT_RE.match(v)
        if m:
            month, day = int(m.group(2)), int(m.group(3))
            if 1 <= month <= 12 and 1 <= day <= 31:
                return Match(self.type_key, self.display_name, 0.55,
                            evidence="YYMMDD 6자리 날짜 패턴", priority=self.priority, sensitivity=self.sensitivity)
        return None
