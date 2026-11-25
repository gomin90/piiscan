"""주민등록번호 detector: 13자리 형식 + 체크섬 + 마스킹 인식."""
from __future__ import annotations

import re

from app.detectors.base import Detector, Match, ValueProfile
from app.utils.validators import birth_part_valid, digits_only, resident_checksum

FULL_RE = re.compile(r"^\d{6}-\d{6}-\d$|^\d{13}$|^\d{6}-\d{6}$|^\d{6}-\d{7}$")
MASKED_RE = re.compile(r"^\d{6}[-.][*＊Xx]{3,7}(?:[-.][*＊Xx]*\d*)?$|\d{4}[*＊]{4,8}[-.]\d{3,4}$")


class ResidentIdDetector(Detector):
    type_key = "RESIDENT_ID"
    display_name = "주민등록번호"
    priority = 1
    sensitivity = 1.0
    candidate_tags = frozenset({"digits", "digits_sep", "masked"})
    name_hints = ("주민", "RESIDENT", "NATIONAL_ID", "CITIZEN", "RCNo")

    def _one(self, text: str) -> Match | None:
        if MASKED_RE.match(text):
            return Match(self.type_key, self.display_name, 0.85, masked=True,
                         evidence="마스킹된 주민등록번호 형식", fragment=text,
                         priority=self.priority, sensitivity=self.sensitivity)
        if not FULL_RE.match(text):
            return None
        digits = digits_only(text)
        if len(digits) < 12 or not birth_part_valid(digits[:6]):
            return None
        if len(digits) == 13:
            valid = resident_checksum(digits)
            return Match(self.type_key, self.display_name, 0.98 if valid else 0.6,
                         evidence=f"13자리 + 체크섬 {'valid' if valid else 'invalid'}",
                         fragment=text, priority=self.priority, sensitivity=self.sensitivity)
        return Match(self.type_key, self.display_name, 0.6,
                     evidence="12자리 (구형) 주민등록번호 형식",
                     fragment=text, priority=self.priority, sensitivity=self.sensitivity)

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        return self._one(value.strip())

    #: Unanchored so a resident number can sit inside a longer cell.
    INNER_RE = re.compile(r"(?<!\d)\d{6}-\d{6,7}(?:-\d)?(?!\d)|(?<!\d)\d{13}(?!\d)")

    def find_matches(self, value: str, profile: ValueProfile, column_name: str = "",
                     context=None) -> list[Match]:
        """Every resident-number occurrence in the cell, with its offsets."""
        whole = self._one((value or "").strip())
        if whole is not None:
            return self._offset(whole, value)
        out: list[Match] = []
        for index, found in enumerate(self.INNER_RE.finditer(value or ""), start=1):
            match = self._one(found.group(0))
            if match is None:
                continue
            match.match_start = found.start()
            match.match_end = found.end()
            match.occurrence_index = index
            out.append(match)
        return out
