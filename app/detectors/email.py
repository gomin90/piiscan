"""이메일 detector (원본 및 마스킹된 형태)."""
from __future__ import annotations

import re

from app.detectors.base import EMAIL_RE, Detector, Match, ValueProfile

MASKED_EMAIL_RE = re.compile(r"[A-Za-z0-9_]{1,10}[*＊]{2,}@[A-Za-z0-9.*＊-]{2,}")


class EmailDetector(Detector):
    type_key = "EMAIL"
    display_name = "이메일"
    priority = 1
    sensitivity = 0.85
    candidate_tags = frozenset({"email"})
    name_hints = ("EMAIL", "MAIL", "이메일", "E_MAIL")

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        v = value.strip()
        if not EMAIL_RE.fullmatch(v) and not MASKED_EMAIL_RE.fullmatch(v):
            return None
        local, domain = v.split("@", 1)
        masked = "*" in local or "*" in domain
        return Match(self.type_key, self.display_name, 0.75 if masked else 0.97,
                     masked=masked, evidence="이메일 형식", priority=self.priority, sensitivity=self.sensitivity)
