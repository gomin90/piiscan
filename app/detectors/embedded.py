"""Detectors for personal data embedded inside long free-text cells.

A JSON document, a log line or a Base64 payload is not itself a phone number, so
whole-cell detectors correctly reject it. Personal data can still sit *inside*
such a cell, and the row-level requirement is that every non-empty cell is
inspected. These detectors search the interior of long cells for strong,
checksum-verified patterns.
"""
from __future__ import annotations

import re

from app.detectors.base import Detector, Match, ValueProfile
from app.utils.validators import birth_part_valid, digits_only, resident_checksum, luhn_valid

LONG_TEXT_TAG = "long_text"
MIN_EMBEDDED_LENGTH = 20

EMBEDDED_RESIDENT = re.compile(r"(?<!\d)\d{6}-\d{6,7}(?:-\d)?(?!\d)")
EMBEDDED_PHONE = re.compile(r"(?<!\d)01[0-9]-\d{3,4}-\d{4}(?!\d)")
EMBEDDED_CARD = re.compile(r"(?<!\d)(?:\d{4}[- ]){3}\d{4}(?!\d)")
EMBEDDED_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
EMBEDDED_PASSPORT = re.compile(r"(?<![A-Za-z0-9])[EQ][A-Z]\d{7,8}(?![A-Za-z0-9])")


class EmbeddedDetector(Detector):
    """Base class: only runs on cells long enough to be free text."""

    candidate_tags = frozenset({LONG_TEXT_TAG})

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        return None


class EmbeddedResidentIdDetector(EmbeddedDetector):
    type_key = "RESIDENT_ID"
    display_name = "주민등록번호"
    priority = 1
    sensitivity = 1.0
    name_hints = ("주민", "RESIDENT", "NATIONAL_ID")

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        found = EMBEDDED_RESIDENT.search(value)
        if not found:
            return None
        digits = digits_only(found.group(0))
        if not birth_part_valid(digits[:6]):
            return None
        if len(digits) == 13:
            valid = resident_checksum(digits)
            return Match(self.type_key, self.display_name, 0.95 if valid else 0.6,
                         evidence=f"긴文本 내부 13자리 주민등록번호 (check_sum {'valid' if valid else 'invalid'})",
                         fragment=found.group(0), priority=self.priority, sensitivity=self.sensitivity)
        return Match(self.type_key, self.display_name, 0.6,
                     evidence="긴文本 내부 12자리 주민등록번호 형식",
                     fragment=found.group(0), priority=self.priority, sensitivity=self.sensitivity)


class EmbeddedPhoneDetector(EmbeddedDetector):
    type_key = "PHONE_NUMBER"
    display_name = "전화번호"
    priority = 1
    sensitivity = 0.8
    name_hints = ("PHONE", "TEL", "전화")

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        found = EMBEDDED_PHONE.search(value)
        if not found:
            return None
        return Match(self.type_key, self.display_name, 0.9,
                     evidence="긴文本 내부 국내 이동전화 형식",
                     fragment=found.group(0), priority=self.priority, sensitivity=self.sensitivity)


class EmbeddedCardDetector(EmbeddedDetector):
    type_key = "CARD_NUMBER"
    display_name = "카드번호"
    priority = 1
    sensitivity = 0.9
    name_hints = ("CARD", "카드")

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        for found in EMBEDDED_CARD.finditer(value):
            digits = digits_only(found.group(0))
            if len(digits) == 16 and luhn_valid(digits):
                return Match(self.type_key, self.display_name, 0.9,
                             evidence="긴文本 내부 16자리 Luhn valid 카드번호",
                             fragment=found.group(0), priority=self.priority, sensitivity=self.sensitivity)
        return None


class EmbeddedEmailDetector(EmbeddedDetector):
    type_key = "EMAIL"
    display_name = "이메일"
    priority = 1
    sensitivity = 0.8
    name_hints = ("EMAIL", "이메일")

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        found = EMBEDDED_EMAIL.search(value)
        if not found:
            return None
        return Match(self.type_key, self.display_name, 0.9,
                     evidence="긴文本 내부 이메일 주소",
                     fragment=found.group(0), priority=self.priority, sensitivity=self.sensitivity)


class EmbeddedPassportDetector(EmbeddedDetector):
    type_key = "PASSPORT"
    display_name = "여권번호"
    priority = 2
    sensitivity = 0.8
    name_hints = ("PASSPORT", "여권")

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        found = EMBEDDED_PASSPORT.search(value)
        if not found:
            return None
        return Match(self.type_key, self.display_name, 0.6,
                     evidence="긴文本 내부 여권번호 형식",
                     fragment=found.group(0), confirmed=False,
                     priority=self.priority, sensitivity=self.sensitivity)
