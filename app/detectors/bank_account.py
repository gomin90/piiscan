"""은행계좌번호 detector: 은행별 형식 + 체크섬."""
from __future__ import annotations

import re

from app.detectors.base import Detector, Match, ValueProfile
from app.utils.validators import digits_only

# Supported account layouts: segment-separated accounts, checksum-valid 12자,
# or plain 10-14 digits only when the column name actually says "account".
SEGMENT_RE = re.compile(r"^\d{2,4}-\d{4,8}-\d{1,4}$|^\d{1,5}-\d{6,10}$|^\d{3}-\d{6,8}-\d{4,6}$")
PLAIN_RE = re.compile(r"^\d{10,14}$")
MASKED_RE = re.compile(r"^\d{4,6}[- ]?\d{3,4}[- ]?[*＊]{3,6}$|^\d{2}-[*＊]{4,8}-\d{2,4}$")
# Exact shapes that belong to another detector; without an account column name
# these must not be read as bank accounts.
OTHER_FORM_RE = re.compile(r"^\d{3}-\d{3,4}-\d{4}$|^\d{2}-\d{6}-\d{4}$")  # phone / NHI
BANK_WEIGHTS = (2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4)


def bank_checksum_valid(digits: str) -> bool:
    if len(digits) != 12:
        return False
    total = sum(w * int(d) for w, d in zip(BANK_WEIGHTS, digits[:11]))
    return (10 - total % 10) % 10 == int(digits[11])


class BankAccountDetector(Detector):
    type_key = "BANK_ACCOUNT"
    display_name = "은행계좌번호"
    priority = 1
    sensitivity = 0.85
    candidate_tags = frozenset({"digits", "digits_sep", "masked"})
    name_hints = ("ACCOUNT", "계좌", "BANK", "ACCT")

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        v = value.strip()
        if MASKED_RE.match(v):
            return Match(self.type_key, self.display_name, 0.65, masked=True,
                        evidence="마스킹된 계좌번호 형식", priority=self.priority, sensitivity=self.sensitivity)
        if not SEGMENT_RE.match(v) and not PLAIN_RE.match(v):
            return None
        digits = digits_only(v)
        if bank_checksum_valid(digits):
            return Match(self.type_key, self.display_name, 0.9,
                        evidence="12자리 + bank checksum valid",
                        priority=self.priority, sensitivity=self.sensitivity)
        if SEGMENT_RE.match(v) and not OTHER_FORM_RE.match(v):
            return Match(self.type_key, self.display_name, 0.75,
                        evidence="계좌 번호 segment 형식",
                        priority=self.priority, sensitivity=self.sensitivity)
        # A bare run of digits is not enough: it needs an account-like column name.
        if self.column_affinity(column_name) >= 0.35:
            return Match(self.type_key, self.display_name, 0.6,
                         evidence="계좌 컬럼 이름 + 10-14자리 숫자 (체크섬 없음)",
                         priority=self.priority, sensitivity=self.sensitivity)
        return None
