"""국가보험번호 / 국민ID 계열 detector."""
from __future__ import annotations

import re

from app.detectors.base import Detector, Match, ValueProfile
from app.utils.validators import digits_only

NHI_RE = re.compile(r"^\d{2}-\d{6}-\d{4}$|^\d{12,13}$")
MASKED_RE = re.compile(r"^\d{2}-\d{6}-[*＊]{4}$|^\d{2}-[*＊]{6}-\d{4}$")


class InsuranceNumberDetector(Detector):
    type_key = "INSURANCE_NUMBER"
    display_name = "국가보험번호"
    priority = 1
    sensitivity = 0.8
    candidate_tags = frozenset({"digits", "digits_sep", "masked"})
    name_hints = ("INSUR", "NHI", "HEALTH", "건강", "보험", "POLICY", "MEMBER_NO")

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        v = value.strip()
        if MASKED_RE.match(v):
            return Match(self.type_key, self.display_name, 0.65, masked=True,
                        evidence="마스킹된 보험번호 형식", priority=self.priority, sensitivity=self.sensitivity)
        if not NHI_RE.match(v):
            return None
        digits = digits_only(v)
        hint = 0.15 if any(h in value.upper() for h in ("INSUR", "NHI", "HEALTH")) else 0.0
        return Match(self.type_key, self.display_name, min(0.55 + hint, 0.85),
                     evidence="12-13자리 국가보험번호 형식", priority=self.priority, sensitivity=self.sensitivity)
