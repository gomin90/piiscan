"""IMSI detector (15자리, MCC 유효성)."""
from __future__ import annotations

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
        if luhn_valid(v):
            # 15자 + Luhn 통과값은 IMEI detector가 확정한다 (Luhn이 IMEI의 정의 조건).
            return None
        return Match(self.type_key, self.display_name, 0.85,
                     evidence=f"MCC {v[:3]} + 15자리 IMSI", priority=self.priority, sensitivity=self.sensitivity)
