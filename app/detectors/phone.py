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
