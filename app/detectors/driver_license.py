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
            # The hyphenated 8-2 layout is the printed domestic form and is
            # specific enough to confirm on its own.
            year = int(digits[:4])
            # A 10-digit run that does not begin with a plausible issuance year
            # (1234567890) is an order/SN value, never a domestic licence.
            if len(digits) == 10 and not (1900 <= year <= 2025):
                return None
            if "-" in v and len(digits) == 10 and 1900 <= year <= 2100:
                # An issuance year in the future can not be a domestic licence.
                if year <= 2025:
                    return Match(self.type_key, self.display_name, 0.88,
                                 evidence="8-2 자리 국내 면허번호 형식",
                                 priority=self.priority, sensitivity=self.sensitivity)
                return Match(self.type_key, self.display_name, 0.6, confirmed=False,
                             evidence=f"면허 형식 nhưng 발급년 {year} 비현실적 (검토 필요)",
                             priority=self.priority, sensitivity=self.sensitivity)
            # A bare 10-digit run also fits foreigner IDs, order numbers and
            # internal keys, so it needs the column name to back it up.
            if len(digits) == 10 and self.column_affinity(column_name) >= 0.35 and year <= 2025:
                return Match(self.type_key, self.display_name, 0.7,
                            evidence="면허 컬럼 이름 + 10자리 숫자",
                            priority=self.priority, sensitivity=self.sensitivity)
            if len(digits) == 10:
                return Match(self.type_key, self.display_name, 0.6, confirmed=False,
                            evidence="10자리 숫자: 면허번호와 내부 코드 구분 불가 (검토 필요)",
                            priority=self.priority, sensitivity=self.sensitivity)
            return None
        if OLD_RE.match(v) and self.column_affinity(column_name) >= 0.35:
            return Match(self.type_key, self.display_name, 0.55,
                        evidence="구형 면허번호 형식 (컬럼 이름 근거)",
                        priority=self.priority, sensitivity=self.sensitivity)
        return None
