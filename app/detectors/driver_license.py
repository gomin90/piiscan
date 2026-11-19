""" 운전면허번호 detector (국내 형식)."""
from __future__ import annotations

import re

from app.detectors.base import Detector, Match, ValueProfile
from app.utils.validators import digits_only

KR_RE = re.compile(r"^\d{8}-\d{2}$|^\d{10}$")
OLD_RE = re.compile(r"^[A-Z]\d{8,9}$")
MASKED_RE = re.compile(r"^\d{4}[*＊]{4}-\d{2}$|^\d{8}-[*＊]{2}$")


class DriverLicenseDetector(Detector):
    type_key = "DRIVER_LICENSE"
    display_name = "운전면허번호"
    priority = 1
    sensitivity = 0.8
    candidate_tags = frozenset({"digits", "digits_sep", "latin", "masked"})
    name_hints = ("면허", "DRIVER", "LICENSE", "DL_NO", "LICENSE_NO", "DRV")

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        v = value.strip().upper()
        if MASKED_RE.match(v):
            return Match(self.type_key, self.display_name, 0.7, masked=True,
                        evidence="운전면허번호 마스킹 패턴", priority=self.priority, sensitivity=self.sensitivity)
        if KR_RE.match(v):
            digits = digits_only(v)
            # 국내면허번호: 8자리(발급년+순번) + 2자리(지청)
            if len(digits) == 10 and 1900 <= int(digits[:4]) <= 2100:
                return Match(self.type_key, self.display_name, 0.88,
                            evidence="8-2 자리 국내 면허번호 형식", priority=self.priority, sensitivity=self.sensitivity)
            return Match(self.type_key, self.display_name, 0.6,
                        evidence="10자리 숫자 (면허번호 가능성)", priority=self.priority, sensitivity=self.sensitivity)
        if OLD_RE.match(v):
            return Match(self.type_key, self.display_name, 0.55,
                        evidence="구형 면허번호 형식", priority=self.priority, sensitivity=self.sensitivity)
        return None
