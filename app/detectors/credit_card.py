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

    def _one(self, text: str) -> Match | None:
        if MASKED_RE.match(text) and len(digits_only(text)) >= 6:
            return Match(self.type_key, self.display_name, 0.7, masked=True,
                         evidence="마스킹된 카드번호 형식", fragment=text,
                         priority=self.priority, sensitivity=self.sensitivity)
        if not CARD_RE.match(text):
            return None
        digits = digits_only(text)
        if len(digits) < 13 or len(digits) > 19:
            return None
        if digits[0] not in "3456":
            return None
        if not text.isdigit():
            # Card numbers written in groups ("4111-1111-1111-1111") keep their
            # verdict even when the check digit is synthetic.
            return Match(self.type_key, self.display_name, 0.75,
                        evidence="카드 번호 segment 형식",
                        fragment=text, priority=self.priority, sensitivity=self.sensitivity)
        if luhn_valid(digits):
            return Match(self.type_key, self.display_name, 0.99,
                        evidence="카드 BIN 형식 + Luhn valid",
                        fragment=text, priority=self.priority, sensitivity=self.sensitivity)
        # A bare run of 13-19 digits that fails Luhn is only a *possible* card:
        # it may be an order number, an account guid or an internal key.
        return Match(self.type_key, self.display_name, 0.55, confirmed=False,
                     evidence="카드 BIN 형식지만 Luhn invalid (문맥 확인 필요)",
                     fragment=text, priority=self.priority, sensitivity=self.sensitivity)

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        return self._one(value.strip())

    def contextual_match(self, match: Match, column_name: str, value: str = "") -> Match:
        #: IMSI / IMEI / MAC columns hold device numbers. A bare digit run there
        #: is not card evidence even when the digits happen to pass Luhn.
        fragment = (match.fragment or value or "").strip()
        if (self.device_identifier_column(column_name)
                and self.column_affinity(column_name) == 0.0
                and fragment.isdigit()):
            return None
        return super().contextual_match(match, column_name, value)

    #: Unanchored PAN pattern so several cards can live in one cell.
    INNER_RE = re.compile(r"(?<!\d)(?:\d{4}[- ]){3}\d{4}(?!\d)|(?<!\d)\d{16}(?!\d)")

    def find_matches(self, value: str, profile: ValueProfile, column_name: str = "",
                     context=None) -> list[Match]:
        whole = self._one((value or "").strip())
        if whole is not None:
            return self._offset(whole, value)
        out: list[Match] = []
        seen: set[tuple[int, int]] = set()
        for index, found in enumerate(self.INNER_RE.finditer(value or ""), start=1):
            span = (found.start(), found.end())
            if span in seen:
                continue
            seen.add(span)
            match = self._one(found.group(0))
            if match is None:
                continue
            match.match_start, match.match_end, match.occurrence_index = span[0], span[1], index
            out.append(match)
        return out
