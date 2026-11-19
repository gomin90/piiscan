"""은행계좌번호 detector: 은행별 형식 + 체크섬."""
from __future__ import annotations

import re

from app.detectors.base import Detector, Match, ValueProfile
from app.utils.validators import digits_only

# Supported account layouts: plain 10-12 digits or segment-separated
ACCOUNT_RE = re.compile(r"^\d{10,14}$|^\d{1,5}-\d{6,10}$|^\d{3}-\d{6,8}-\d{4,6}$")
MASKED_RE = re.compile(r"^\d{3}-\d{4}[*＊]{4,6}$|^\d{2}-[*＊]{6}-\d{4}$")
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
        if not ACCOUNT_RE.match(v):
            return None
        digits = digits_only(v)
        if len(digits) < 10 or len(digits) > 14:
            return None
        valid = bank_checksum_valid(digits)
        return Match(self.type_key, self.display_name, 0.9 if valid else 0.6,
                     evidence=f"계좌 형식 + 체크섬 {'valid' if valid else 'invalid'}",
                     priority=self.priority, sensitivity=self.sensitivity)
