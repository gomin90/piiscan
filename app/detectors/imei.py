"""IMEI detector (15자리 + Luhn)."""
from __future__ import annotations

from app.detectors.base import Detector, Match, ValueProfile
from app.utils.validators import luhn_valid


class ImeiDetector(Detector):
    type_key = "IMEI"
    display_name = "IMEI"
    priority = 2
    sensitivity = 0.5
    candidate_tags = frozenset({"digits"})
    name_hints = ("IMEI", "DEVICE", "TERMINAL")

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        v = value.strip()
        if len(v) == 15 and v.isdigit() and luhn_valid(v):
            return Match(self.type_key, self.display_name, 0.85,
                        evidence="15자리 + Luhn valid", priority=self.priority, sensitivity=self.sensitivity)
        return None
