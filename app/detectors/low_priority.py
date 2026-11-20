"""낮은 우선순위 탐지기: SN, UUID, MAC, IP, Postcode."""
from __future__ import annotations

from app.detectors.base import (
    Detector,
    Match,
    MAC_RE,
    UUID_RE,
    IPV4_RE,
    ValueProfile,
)

import re

SN_RE = re.compile(r"^[A-Z0-9]{4,24}$")
POSTCODE_KR = re.compile(r"^\d{5}$|^\d{3}[ -]\d{3}$|^\d{7}$")
POSTCODE_GENERIC = re.compile(r"^[A-Z]{1,2}\d{1,4}([ -]\d[A-Z]{2})?$|^\d{5,6}$")


class SerialNumberDetector(Detector):
    type_key = "SN"
    display_name = "SN"
    priority = 2
    sensitivity = 0.2
    candidate_tags = frozenset({"latin", "digits", "dot_token", "alnum"})
    name_hints = ("SN", "SERIAL", "SEQ_NO", "RND")

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        v = value.strip().upper()
        if "UUID" in profile.tags or "MAC" in profile.tags:
            return None
        hinted = self.column_affinity(column_name) >= 0.35
        if "-" not in v:
            # A serial mixes letters and digits; pure digit runs or passport-like
            # "A12345678" strings belong to other detectors.
            letters = sum(ch.isalpha() for ch in v)
            digits = sum(ch.isdigit() for ch in v)
            if SN_RE.fullmatch(v) and len(v) >= 6 and letters >= 2 and digits >= 2:
                return Match(self.type_key, self.display_name, 0.6 if hinted else 0.5,
                             evidence="시리얼/Serial 패턴", priority=self.priority, sensitivity=self.sensitivity)
            return None
        if hinted and re.fullmatch(r"[A-Z0-9]{2,6}(-[A-Z0-9]{2,6}){1,3}", v):
            return Match(self.type_key, self.display_name, 0.6,
                         evidence="시리얼 segment 형식 (SERIAL 컬럼 근거)",
                         priority=self.priority, sensitivity=self.sensitivity)
        return None


class UuidDetector(Detector):
    type_key = "UUID"
    display_name = "UUID"
    priority = 2
    sensitivity = 0.2
    candidate_tags = frozenset({"uuid"})
    name_hints = ("UUID", "GUID", "GUID")

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        if UUID_RE.fullmatch(value.strip()):
            return Match(self.type_key, self.display_name, 0.9,
                        evidence="UUID v1/v4 형식", priority=self.priority, sensitivity=self.sensitivity)
        return None


class MacAddressDetector(Detector):
    type_key = "MAC"
    display_name = "MAC Address"
    priority = 2
    sensitivity = 0.3
    candidate_tags = frozenset({"mac"})
    name_hints = ("MAC", "MAC_ADDR")

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        if MAC_RE.fullmatch(value.strip()):
            return Match(self.type_key, self.display_name, 0.9,
                        evidence="MAC 주소 형식", priority=self.priority, sensitivity=self.sensitivity)
        return None


class IpAddressDetector(Detector):
    type_key = "IP"
    display_name = "IP"
    priority = 2
    sensitivity = 0.3
    candidate_tags = frozenset({"ip"})
    name_hints = ("IP", "HOST", "CLIENT_IP")

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        v = value.strip()
        if MAC_RE.fullmatch(v) or re.fullmatch(r"([0-9a-fA-F]{2}:){4,5}[0-9a-fA-F]{2}", v):
            return None
        if IPV4_RE.fullmatch(v):
            parts = [int(p) for p in v.split(".")]
            if all(0 <= p <= 255 for p in parts):
                return Match(self.type_key, self.display_name, 0.9,
                            evidence="IPv4 주소 형식", priority=self.priority, sensitivity=self.sensitivity)
        if re.fullmatch(r"[0-9a-fA-F:]{4,39}", v) and v.count(":") >= 2:
            return Match(self.type_key, self.display_name, 0.7,
                        evidence="IPv6 주소 형식", priority=self.priority, sensitivity=self.sensitivity)
        return None


class PostcodeDetector(Detector):
    type_key = "POSTCODE"
    display_name = "Postcode"
    priority = 2
    sensitivity = 0.3
    candidate_tags = frozenset({"digits", "digits_sep", "latin"})
    name_hints = ("POST", "ZIP", "우편", "PCODE")
    negative_hints = ("AMOUNT", "PRICE", "TOTAL", "QTY", "COUNT", "SEQ", "IDX", "BALANCE", "SUM", "金额")

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        v = value.strip()
        if self.name_excludes(column_name):
            # A numeric amount/counter column is not a postcode column, however
            # five digits long its values happen to be.
            return None
        if POSTCODE_KR.fullmatch(v):
            return Match(self.type_key, self.display_name, 0.8,
                        evidence="우편번호 형식", priority=self.priority, sensitivity=self.sensitivity)
        if POSTCODE_GENERIC.fullmatch(v) and len(v) >= 4:
            # Bare 6-digit runs are usually YYMMDD dates or IDs, not postcodes.
            if v.isdigit() and len(v) == 6:
                if 1 <= int(v[2:4]) <= 12 and 1 <= int(v[4:6]) <= 31:
                    return None
                if self.column_affinity(column_name) < 0.35:
                    return None
            return Match(self.type_key, self.display_name, 0.55,
                        evidence="국가별 postcode 형식", priority=self.priority, sensitivity=self.sensitivity)
        return None
