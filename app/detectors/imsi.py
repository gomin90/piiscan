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
        # 15자리 + Luhn 통과이면 IMEI로 더 Strong -> IMSI는 MCC 기반으로 판정
        if luhn_valid(v) and 200 <= int(v[:3]) <= 799 and v[3:].startswith("0"):
            return Match(self.type_key, self.display_name, 0.6,
                        evidence="15자리 (IMSI/IMEI ambiguous)", priority=self.priority, sensitivity=self.sensitivity)
        return Match(self.type_key, self.display_name, 0.85,
                     evidence=f"MCC {v[:3]} + 15자리 IMSI", priority=self.priority, sensitivity=self.sensitivity)
