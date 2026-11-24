"""Person-name detection inside free-text cells.

Hangul prose is not evidence of a name on its own: "배송 처리 완료" contains no
person. A name inside a sentence is only claimed when it is anchored by a known
Korean surname (or is a Latin full name after a title) *and* it sits next to a
person label ("담당자: 홍길동"), an honorific ("홍길동 고객님") or a predicate
("담당자는 홍길동입니다").

Whole-cell names are handled by NameDetector; this detector covers names embedded
in longer text, and records only the matched fragment.
"""
from __future__ import annotations

import re

from app.detectors.base import Detector, Match, ValueProfile
from app.detectors.name import KOREAN_SURNAMES

SURNAME = "|".join(sorted(KOREAN_SURNAMES))
# surname + 1-2 syllables given name, not glued to a following verb
KOREAN_NAME_RE = re.compile(rf"(?:{SURNAME})(?:[가-힣]{{1,2}})(?:(?:님|씨| 님| 닒)?|(?=\s|[:,.;!?]|$))")
LABEL_BEFORE_RE = re.compile(
    rf"(?:담당자|담당|성명|姓名|이름|고객명|고객| 주문자|신청자|작성자|문의|연락|"
    rf"manager|contact|name|receiver|recipient|patient|attendee)"
    rf"(?:[ 은는이가 의]*)?[:=]?\s*((?:{SURNAME})[가-힣]{{1,2}})",
    re.IGNORECASE,
)
HONORIFIC_RE = re.compile(rf"((?:{SURNAME})[가-힣]{{1,2}})(?:\s+(?:고객님|씨|님|선생님|사장|先生))")
PREDICATE_RE = re.compile(rf"((?:{SURNAME})[가-힣]{{1,2}})(?:님)?(?:은|는)?(?:입니다|이다|께서|에게|의)")
TITLE_NAME_RE = re.compile(r"\b(?:Mr|Mrs|Ms|Dr)\.?\s+([A-Z][a-z]{1,12}\s[A-Z][a-z]{1,12})\b")


class FreeTextNameDetector(Detector):
    type_key = "NAME"
    display_name = "이름"
    priority = 1
    sensitivity = 0.7
    candidate_tags = frozenset({"hangul", "latin"})
    name_hints = ()

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        v = value.strip()
        # Single-token cells are already covered by the whole-cell name detector.
        if len(v) < 6 or " " not in v:
            return None
        for pattern, confidence, evidence in (
            (LABEL_BEFORE_RE, 0.9, "문장 내 이름 (담당자/성명/contact label)"),
            (HONORIFIC_RE, 0.9, "문장 내 이름 + 칭호(씨/님/고객님)"),
            (TITLE_NAME_RE, 0.85, "문장 내 Full name (Mr./Ms.)"),
            (PREDICATE_RE, 0.6, "문장 내 이름 predicate (~입니다/이다)"),
        ):
            found = pattern.search(v)
            if found:
                name = found.group(1).strip()
                if len(name) >= 2:
                    # A name glued to a person label or an honorific is direct
                    # evidence; the predicate-only form stays for the AI/human pass.
                    confirmed = confidence >= 0.85
                    return Match(self.type_key, self.display_name, confidence,
                                 evidence=evidence, fragment=name, confirmed=confirmed,
                                 priority=self.priority, sensitivity=self.sensitivity)
        return None
