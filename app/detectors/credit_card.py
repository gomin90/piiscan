"""카드번호 detector: BIN 패턴 + Luhn 체크섬."""
from __future__ import annotations

import re

from app.detectors.base import Detector, Match, ValueProfile
from app.utils.validators import digits_only, luhn_valid

CARD_RE = re.compile(r"^\d{13,19}$|^\d{4}[- ]\d{4}[- ]\d{4}[- ]\d{4}$|^\d{4}[- ]\d{6}[- ]\d{4}$")
MASKED_RE = re.compile(r"^[3-6]\d{3}[- ]?\d{1,6}[- ]?[*＊Xx]{3,10}[- ]?\d{0,4}$")


class CreditCardDetector(Detector):
    type_key = "CARD_NUMBER"
    display_name = "카드번호"
    priority = 1
    sensitivity = 1.0
    candidate_tags = frozenset({"digits", "digits_sep", "masked"})
    name_hints = ("CARD", "카드", "PAN", "ACCOUNT_CARD")

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        v = value.strip()
        if MASKED_RE.match(v) and len(digits_only(v)) >= 6:
            return Match(self.type_key, self.display_name, 0.7, masked=True,
                        evidence="마스킹된 카드번호 형식", priority=self.priority, sensitivity=self.sensitivity)
        if not CARD_RE.match(v):
            return None
        digits = digits_only(v)
        if len(digits) < 13 or len(digits) > 19:
            return None
        bin_ok = digits[0] in "3456"
        if not bin_ok:
            return None
        valid = luhn_valid(digits)
        return Match(self.type_key, self.display_name, 0.99 if valid else 0.7,
                     evidence=f"BIN {digits[:4]} + Luhn {'valid' if valid else 'invalid'}",
                     priority=self.priority, sensitivity=self.sensitivity)
