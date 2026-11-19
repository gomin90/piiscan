"""사회보장번호(SSN) 및 국가별 신분번호 detector."""
from __future__ import annotations

import re

from app.detectors.base import Detector, Match, ValueProfile
from app.utils.validators import digits_only

SSN_RE = re.compile(r"^\d{3}-\d{2}-\d{4}$")
SSN_PLAIN_RE = re.compile(r"^\d{9}$")
MASKED_RE = re.compile(r"^\d{3}-[*＊]{2}-\d{4}$|^\d{3}-\d{2}-[*＊]{4}$")


class SocialSecurityDetector(Detector):
    type_key = "SOCIAL_SECURITY"
    display_name = "사회보장번호"
    priority = 1
    sensitivity = 0.9
    candidate_tags = frozenset({"digits", "digits_sep", "masked"})
    name_hints = ("SSN", "SOCIAL", "社保", "SECURITY_NO")

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        v = value.strip()
        if SSN_RE.match(v):
            area, group, serial = v.split("-")
            if area != "000" and group != "00" and serial != "0000":
                return Match(self.type_key, self.display_name, 0.9,
                            evidence="US SSN 형식 (area/group/serial)", priority=self.priority, sensitivity=self.sensitivity)
        if SSN_PLAIN_RE.match(v):
            return Match(self.type_key, self.display_name, 0.45,
                        evidence="9자리 숫자 (SSN 가능성)", priority=self.priority, sensitivity=self.sensitivity)
        if MASKED_RE.match(v):
            return Match(self.type_key, self.display_name, 0.7, masked=True,
                        evidence="마스킹된 SSN 형식", priority=self.priority, sensitivity=self.sensitivity)
        return None
