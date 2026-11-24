"""생년월일 detector."""
from __future__ import annotations

import re

from app.detectors.base import Detector, Match, ValueProfile
from app.utils.validators import plausible_date

ISO_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T]\d{1,2}:\d{2}(:\d{2})?)?$")
COMPACT_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
SHORT_RE = re.compile(r"^(\d{2})(\d{2})(\d{2})$")
MASKED_RE = re.compile(r"^\d{4}[-/]\d{2}[-/][*＊]{2}$|^\d{2}[-/][*＊]{2}[-/]\d{2}$")
DATE_COLUMN_RE = re.compile(r"(DATE|TIME|YEAR|PERIOD|DAY)$")
#: Words that make a date inside free text a birth date rather than an event date.
BIRTH_CONTEXT_RE = re.compile(
    r"(?:출생|出生|생일|born|birth|birthday|DOB|date of birth|生年月日|태年月日)",
    re.IGNORECASE,
)
INNER_DATE_RE = re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})|(\d{4})(\d{2})(\d{2})")


class BirthDateDetector(Detector):
    type_key = "BIRTH_DATE"
    display_name = "생년월일"
    priority = 1
    sensitivity = 0.7
    candidate_tags = frozenset({"digits", "digits_sep", "separated", "long_text"})
    name_hints = ("BIRTH", "DOB", "생년", "출생", "BIRTHDAY", "YEARS", "NATIVE")
    # A plain ISO date in a transaction/log column is an event date, not a birthday.
    negative_hints = ("EVENT", "ORDER", "CREATED", "UPDATED", "REG", "DEPART", "LOG",
                      "SYSTEM", "TXN", "EXPIRE", "SHIP", "DELIVER", "PAY", "OPEN", "CLOSE")

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        v = value.strip().replace("/", "-")
        birth_hint = self.column_affinity(column_name) > 0.0 or bool(BIRTH_CONTEXT_RE.search(value))
        #: A bare date in an event/transaction column is an event date, not a
        #: birthday; only a birth label (header or in the cell) promotes it.
        #: A date carrying a clock time is a timestamp, never a birthday.
        if not birth_hint and (self.name_excludes(column_name)
                               or DATE_COLUMN_RE.search(column_name.upper())
                               or re.search(r"\d{1,2}:\d{2}", v)):
            return None
        if MASKED_RE.match(v):
            return Match(self.type_key, self.display_name, 0.65, masked=True,
                        evidence="마스킹된 생년월일", priority=self.priority, sensitivity=self.sensitivity)
        m = ISO_RE.match(v) or COMPACT_RE.match(v)
        if m:
            year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if plausible_date(year, month, day):
                return Match(self.type_key, self.display_name, 0.9,
                            evidence="ISO/8자리 생년월일", priority=self.priority, sensitivity=self.sensitivity)
        #: "출생일 1985-03-15" - the date is buried in free text, so the anchored
        #: patterns above never see it.
        if birth_hint:
            found = INNER_DATE_RE.search(v)
            if found:
                groups = [g for g in found.groups() if g]
                year, month, day = int(groups[0]), int(groups[1]), int(groups[2])
                if plausible_date(year, month, day):
                    return Match(self.type_key, self.display_name, 0.9,
                                 evidence="문장 내 생년월일 (출생/birth 문맥)",
                                 fragment=found.group(0), priority=self.priority,
                                 sensitivity=self.sensitivity)
        m = SHORT_RE.match(v)
        if m:
            month, day = int(m.group(2)), int(m.group(3))
            if 1 <= month <= 12 and 1 <= day <= 31:
                return Match(self.type_key, self.display_name, 0.55,
                            evidence="YYMMDD 6자리 날짜 패턴", priority=self.priority, sensitivity=self.sensitivity)
        return None
