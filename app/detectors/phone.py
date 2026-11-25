"""전화번호 detector: 국내 및 해외 형식."""
from __future__ import annotations

import re

from app.detectors.base import Detector, Match, ValueProfile
from app.utils.validators import digits_only

KR_MOBILE = re.compile(r"^01[0-9]-\d{3,4}-\d{4}$|^01[0-9]\d{7,8}$")
KR_LANDLINE = re.compile(r"^0\d{1,3}-\d{3,4}-\d{4}$")
INTL = re.compile(r"^\+\d{1,3}[-\s]?(\d{1,4}[-\s]?){2,5}$")
MASKED = re.compile(r"^01[0-9]-[*＊]{3,4}-\d{4}$|^01[0-9]-\d{4}-[*＊]{4}$|^01[0-9][*＊]{4,8}$")
OFFICE = re.compile(r"^0\d{2}-\d{4}$")
#: Loose scan used when one cell holds several numbers. The digit look-behind /
#: look-ahead stops a phone-shaped substring being cut out of a longer number
#: run (a 13-digit resident number is not a phone number).
SCAN = re.compile(r"(?<!\d)01[0-9]-\d{3,4}-\d{4}(?!\d)|(?<!\d)01[0-9]\d{7,8}(?!\d)|"
                  r"(?<!\d)0\d{1,3}-\d{3,4}-\d{4}(?!\d)|"
                  r"\+\d{1,3}[-\s]?\d{3,4}[-\s]?\d{3,4}[-\s]?\d{3,4}")


class PhoneDetector(Detector):
    type_key = "PHONE_NUMBER"
    display_name = "전화번호"
    priority = 1
    sensitivity = 0.8
    candidate_tags = frozenset({"digits", "digits_sep", "masked"})
    name_hints = ("PHONE", "TEL", "MOBILE", "CELL", "전화", "휴대폰", "HANDPHONE")

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        v = value.strip()
        if MASKED.match(v):
            return Match(self.type_key, self.display_name, 0.8, masked=True,
                        evidence="마스킹된 국내 전화번호", priority=self.priority, sensitivity=self.sensitivity)
        if KR_MOBILE.match(v):
            return Match(self.type_key, self.display_name, 0.97,
                        evidence="국내 이동전화 형식", priority=self.priority, sensitivity=self.sensitivity)
        if KR_LANDLINE.match(v):
            return Match(self.type_key, self.display_name, 0.9,
                        evidence="국내 고정전화 형식", priority=self.priority, sensitivity=self.sensitivity)
        if INTL.match(v) and len(digits_only(v)) >= 8:
            return Match(self.type_key, self.display_name, 0.85,
                        evidence="해외 전화번호 형식", priority=self.priority, sensitivity=self.sensitivity)
        return None

    def find_matches(self, value: str, profile: ValueProfile, column_name: str = "",
                    context: ScanContext | None = None) -> list[Match]:
        """One Match per phone number, including cells that list several."""
        single = self.match(value, profile, column_name)
        if single is not None:
            return self._offset(single, value)
        out: list[Match] = []
        for index, found in enumerate(SCAN.finditer(value or ""), start=1):
            text = found.group(0)
            if MASKED.match(text):
                confidence, note = 0.8, "마스킹된 국내 전화번호"
            elif KR_MOBILE.match(text):
                confidence, note = 0.97, "국내 이동전화 형식"
            elif KR_LANDLINE.match(text):
                confidence, note = 0.9, "국내 고정전화 형식"
            elif INTL.match(text) and len(digits_only(text)) >= 8:
                confidence, note = 0.85, "해외 전화번호 형식"
            else:
                continue
            out.append(Match(self.type_key, self.display_name, confidence,
                             evidence=f"{note} (Cell 내 {index}번째 occurrence)",
                             fragment=text, priority=self.priority, sensitivity=self.sensitivity,
                             match_start=found.start(), match_end=found.end(),
                             occurrence_index=index))
        return out
