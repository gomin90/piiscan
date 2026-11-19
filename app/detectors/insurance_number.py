"""국가보험번호 / 국민ID 계열 detector."""
from __future__ import annotations

import re

from app.detectors.base import Detector, Match, ValueProfile

NHI_DASH_RE = re.compile(r"^\d{2}-\d{6}-\d{4}$")
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
        if not NHI_DASH_RE.match(v) and not re.fullmatch(r"\d{12,13}", v):
            return None
        # A bare 12-13 digit run is ambiguous (resident / foreigner / account all fit),
        # so it is only reported when the column name claims an insurance number.
        if not NHI_DASH_RE.match(v) and self.column_affinity(column_name) < 0.35:
            return None
        hint = 0.15 if NHI_DASH_RE.match(v) else 0.0
        return Match(self.type_key, self.display_name, min(0.55 + hint, 0.85),
                     evidence="NN-NNNNNN-NNNN 국가보험번호 형식" if NHI_DASH_RE.match(v)
                     else "보험번호 컬럼 이름 + 12-13자리 숫자",
                     priority=self.priority, sensitivity=self.sensitivity)
