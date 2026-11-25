"""IMSI detector (15자리, MCC 유효성)."""
from __future__ import annotations

import re

from app.detectors.base import Detector, Match, ValueProfile
from app.utils.validators import imsi_valid, luhn_valid


class ImsiDetector(Detector):
    type_key = "IMSI"
    display_name = "IMSI"
    priority = 1
    sensitivity = 0.8
    candidate_tags = frozenset({"digits"})
    name_hints = ("IMSI", "Subscriber", "MSISDN")

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        v = value.strip()
        if len(v) != 15 or not v.isdigit():
            return None
        if not imsi_valid(v):
            return None
        if re.match(r"^(34|37)\d{13}$", v) and self.column_affinity(column_name) < 0.30:
            # 15 digits starting 34/37 is the Amex card layout, not a mobile subscriber id.
            return None
        if luhn_valid(v):
            # 15자 + Luhn 통과값은 IMEI detector가 확정한다 (Luhn이 IMEI의 정의 조건).
            return None
        return Match(self.type_key, self.display_name, 0.85,
                     evidence="MCC(3자리) + 15자리 IMSI 형식", priority=self.priority, sensitivity=self.sensitivity)
