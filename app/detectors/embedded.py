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
EMBEDDED_BIRTH = re.compile(r"(?<!\d)(19\d{2}|20\d{2})[-/](\d{1,2})[-/](\d{1,2})(?!\d)")
BIRTH_CUES = ("birth", "dob", "born", "생년", "출생", "生日", "出生", "birthday", "생일")


class EmbeddedDetector(Detector):
    """Base class: only runs on cells long enough to be free text."""

    candidate_tags = frozenset({LONG_TEXT_TAG})

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        return None

    def _occurrences(self, value: str, pattern, build) -> list[Match]:
        """Every non-overlapping hit of `pattern`, numbered left to right."""
        out: list[Match] = []
        index = 0
        for found in pattern.finditer(value or ""):
            match = build(found.group(0))
            if match is None:
                continue
            index += 1
            match.match_start = found.start()
            match.match_end = found.end()
            match.occurrence_index = index
            out.append(match)
        return out


class EmbeddedResidentIdDetector(EmbeddedDetector):
    type_key = "RESIDENT_ID"
    display_name = "주민등록번호"
    priority = 1
    sensitivity = 1.0
    name_hints = ("주민", "RESIDENT", "NATIONAL_ID")

    def _one(self, text: str) -> Match | None:
        digits = digits_only(text)
        if not birth_part_valid(digits[:6]):
            return None
        if len(digits) == 13:
            valid = resident_checksum(digits)
            return Match(self.type_key, self.display_name, 0.95 if valid else 0.6,
                         evidence=f"긴文本 내부 13자리 주민등록번호 (check_sum {'valid' if valid else 'invalid'})",
                         fragment=text, priority=self.priority, sensitivity=self.sensitivity)
        return Match(self.type_key, self.display_name, 0.6,
                     evidence="긴文本 내부 12자리 주민등록번호 형식",
                     fragment=text, priority=self.priority, sensitivity=self.sensitivity)

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        found = EMBEDDED_RESIDENT.search(value)
        return self._one(found.group(0)) if found else None

    def find_matches(self, value: str, profile: ValueProfile, column_name: str = "",
                     context=None) -> list[Match]:
        out = []
        for index, found in enumerate(EMBEDDED_RESIDENT.finditer(value), start=1):
            match = self._one(found.group(0))
            if match is None:
                continue
            match.match_start = found.start()
            match.match_end = found.end()
            match.occurrence_index = index
            out.append(match)
        return out


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

    def find_matches(self, value: str, profile: ValueProfile, column_name: str = "",
                     context=None) -> list[Match]:
        def build(text: str) -> Match:
            return Match(self.type_key, self.display_name, 0.9,
                         evidence="긴文本 내부 국내 이동전화 형식",
                         fragment=text, priority=self.priority, sensitivity=self.sensitivity)
        return self._occurrences(value, EMBEDDED_PHONE, build)


class EmbeddedCardDetector(EmbeddedDetector):
    type_key = "CARD_NUMBER"
    display_name = "카드번호"
    priority = 1
    sensitivity = 0.9
    name_hints = ("CARD", "카드")

    def _card(self, text: str) -> Match | None:
        digits = digits_only(text)
        if len(digits) == 16 and luhn_valid(digits):
            return Match(self.type_key, self.display_name, 0.9,
                         evidence="긴文本 내부 16자리 Luhn valid 카드번호",
                         fragment=text, priority=self.priority, sensitivity=self.sensitivity)
        return None

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        for found in EMBEDDED_CARD.finditer(value):
            match = self._card(found.group(0))
            if match is not None:
                return match
        return None

    def find_matches(self, value: str, profile: ValueProfile, column_name: str = "",
                     context=None) -> list[Match]:
        out = []
        for index, found in enumerate(EMBEDDED_CARD.finditer(value), start=1):
            match = self._card(found.group(0))
            if match is None:
                continue
            match.match_start = found.start()
            match.match_end = found.end()
            match.occurrence_index = index
            out.append(match)
        return out


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

    def find_matches(self, value: str, profile: ValueProfile, column_name: str = "",
                     context=None) -> list[Match]:
        return [Match(self.type_key, self.display_name, 0.9,
                      evidence="긴文本 내부 이메일 주소",
                      fragment=found.group(0), priority=self.priority,
                      sensitivity=self.sensitivity,
                      match_start=found.start(), match_end=found.end(),
                      occurrence_index=index)
                for index, found in enumerate(EMBEDDED_EMAIL.finditer(value), start=1)]


class EmbeddedBirthDateDetector(Detector):
    """A date that is explicitly labelled as a birth date inside free text."""

    type_key = "BIRTH_DATE"
    display_name = "생년월일"
    priority = 2
    sensitivity = 0.7
    candidate_tags = frozenset({"digits", "digits_sep", "separated", "long_text", "hangul", "latin"})
    name_hints = ("BIRTH", "DOB", "생년", "출생")

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        text = value or ""
        lowered = text.lower()
        if not any(cue in lowered for cue in BIRTH_CUES):
            return None
        found = EMBEDDED_BIRTH.search(text)
        if not found:
            return None
        year, month, day = int(found.group(1)), int(found.group(2)), int(found.group(3))
        if not birth_part_valid(f"{year}{month:02d}{day:02d}"):
            return None
        return Match(self.type_key, self.display_name, 0.85,
                     evidence="문장 내 생년월일 표시", fragment=found.group(0),
                     priority=self.priority, sensitivity=self.sensitivity)


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


EMBEDDED_MAC = re.compile(r"(?<![\w:])((?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2})(?![\w:])")
EMBEDDED_COORDS = re.compile(
    r"(?<![\w.])(-?\d{1,3}\.\d{3,})\s*(?:,|\s{1,3})\s*(-?\d{1,3}\.\d{3,})(?![\w.])")
EMBEDDED_COORD_LABELS = re.compile(
    r"(?i)lat(?:itude)?[^0-9-]{0,20}(-?\d{1,3}\.\d{3,})[^0-9-]{0,25}long(?:itude)?[^0-9-]{0,20}"
    r"(-?\d{1,3}\.\d{3,})")
EMBEDDED_DEVICE_ID = re.compile(r"(?<!\d)(?:IMEI|IMSI)[^\d]{0,12}(\d{14,15})(?!\d)", re.IGNORECASE)


class EmbeddedMacDetector(EmbeddedDetector):
    type_key = "MAC"
    display_name = "MAC Address"
    priority = 2
    sensitivity = 0.3

    def find_matches(self, value: str, profile: ValueProfile, column_name: str = "",
                     context=None) -> list[Match]:
        return [Match(self.type_key, self.display_name, 0.8,
                      evidence="긴文本 내부 MAC 주소", fragment=found.group(1),
                      priority=self.priority, sensitivity=self.sensitivity,
                      match_start=found.start(), match_end=found.end(),
                      occurrence_index=index)
                for index, found in enumerate(EMBEDDED_MAC.finditer(value or ""), start=1)]


class EmbeddedCoordinateDetector(EmbeddedDetector):
    """위도/경도 쌍 that appear inside a sentence or a log line."""

    type_key = "LOCATION"
    display_name = "위치좌표(위도·경도)"
    priority = 1
    sensitivity = 0.6
    name_hints = ("LAT", "LNG", "LON", "GEO", "COORD", "GPS", "위도", "경도")

    def _pairs(self, value: str):
        seen: set[tuple[str, str]] = set()
        for pattern in (EMBEDDED_COORD_LABELS, EMBEDDED_COORDS):
            for found in pattern.finditer(value or ""):
                lat, lon = found.group(1), found.group(2)
                if not (-90.0 <= float(lat) <= 90.0 and -180.0 <= float(lon) <= 180.0):
                    continue  # an out-of-range pair is not a coordinate
                if (lat, lon) in seen:
                    continue
                seen.add((lat, lon))
                yield found, lat, lon

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        first = next(self._pairs(value), None)
        if first is None:
            return None
        found, lat, lon = first
        return Match(self.type_key, self.display_name, 0.85,
                     evidence="문장 내 위도/경도 쌍", fragment=found.group(0),
                     priority=self.priority, sensitivity=self.sensitivity)

    def find_matches(self, value: str, profile: ValueProfile, column_name: str = "",
                     context=None) -> list[Match]:
        out: list[Match] = []
        for index, (found, lat, lon) in enumerate(self._pairs(value), start=1):
            start, end = found.start(), found.end()
            out.append(Match("LATITUDE", "위도", 0.85, evidence="문장 내 위도값",
                             fragment=lat, priority=self.priority, sensitivity=self.sensitivity,
                             match_start=start, match_end=end, occurrence_index=index))
            out.append(Match("LONGITUDE", "경도", 0.85, evidence="문장 내 경도값",
                             fragment=lon, priority=self.priority, sensitivity=self.sensitivity,
                             match_start=start, match_end=end, occurrence_index=index))
        return out


class EmbeddedDeviceIdDetector(EmbeddedDetector):
    """IMEI / IMSI explicitly labelled inside free text."""

    type_key = "IMEI"
    display_name = "IMEI"
    priority = 1
    sensitivity = 0.85
    name_hints = ("IMEI", "IMSI", "DEVICE")

    def find_matches(self, value: str, profile: ValueProfile, column_name: str = "",
                     context=None) -> list[Match]:
        out: list[Match] = []
        for index, found in enumerate(EMBEDDED_DEVICE_ID.finditer(value or ""), start=1):
            digits = found.group(1)
            key = "IMSI" if "IMSI" in found.group(0).upper() else "IMEI"
            name = "IMSI" if key == "IMSI" else "IMEI"
            out.append(Match(key, name, 0.85,
                             evidence="문장 내 단말 식별번호", fragment=digits,
                             priority=self.priority, sensitivity=self.sensitivity,
                             match_start=found.start(), match_end=found.end(),
                             occurrence_index=index))
        return out
