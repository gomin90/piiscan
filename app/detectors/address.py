"""상세주소 detector: 건물명, 도로명, 동/호수, 층 등 상세 주소."""
from __future__ import annotations

import re

from app.detectors.base import MASK_CHARS, Detector, Match, ValueProfile

# 시·도 또는 시 level alone is NOT a detailed address.
REGION_ONLY = {
    "서울", "서울특별시", "경기", "경기도", "부산", "부산광역시", "인천", "인천광역시",
    "대구", "대구광역시", "광주", "광주광역시", "대전", "대전광역시", "울산", "울산광역시",
    "세종", "세종특별시", "충남", "충청남도", "충북", "충청북도", "전남", "전라남도",
    "전북", "전라북도", "경남", "경상남도", "경북", "강원", "강원도", "제주",
    "제주특별자치도", "시", "군", "구",
}

# Latin road words must be whole words: "ORD42" contains the letters "rd" but is
# not a road, and reading it as one turns every order code into an address.
ROAD_RE = re.compile(r"\b(?:road|street|st|ave|avenue|blvd|rd)\b|로|길|거리", re.IGNORECASE)
DETAIL_RE = re.compile(
    # 12-34 style road number. The lookahead keeps phone-like chains
    # ("010-1234-5678") out: a road number is never followed by another segment.
    r"(?<![-\d])\d{1,4}\s*[-–]\s*\d{1,4}(?![-\d])"
    r"|\d{1,4}\s*(층|호|동|室|層)"          # floor / room / building
    r"|아파트|公寓|빌딩|빌라|building|大厦|号楼"
    r"|(apt|unit|suite|flat|#)\s*[-\d]+"
    r"|(?:\b(?:road|street|st|ave|avenue|blvd|rd)\b|로|길|거리)\s+\d{1,5}",
    re.IGNORECASE,
)
# Number-before-street form ("123 Main St") and romanised Korean roads ("Sequoia-ro 54"),
# which the trailing-number pattern above does not cover.
PRE_POSITION_RE = re.compile(
    r"(?<![-\w])\d{1,5}\s+[A-Za-z]{2,20}\s+(?:St|Street|Ave|Avenue|Rd|Road|Blvd)\b",
    re.IGNORECASE,
)
ROMANISED_ROAD_RE = re.compile(r"[A-Za-z]{3,20}-(?:ro|il)\s+\d{1,5}", re.IGNORECASE)
BUILDING_RE = re.compile(r"아파트|公寓|빌딩|빌라|building|大厦|号楼|apt|unit|suite|flat", re.IGNORECASE)


class AddressDetector(Detector):
    type_key = "ADDRESS"
    display_name = "상세주소"
    priority = 1
    sensitivity = 0.75
    candidate_tags = frozenset({"hangul", "latin", "alnum", "long_text"})
    name_hints = ("ADDR", "ADDRESS", "주소", "STREET", "LOCATION")
    # A column of IDs/refs is not an address column even when a value contains
    # "<number> <word> Street".
    negative_hints = ("ORDER", "REF", "CODE", "ID", "NO", "번호", "코드")

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        v = value.strip()
        if len(v) < 6 or v in REGION_ONLY:
            return None
        # Mostly numeric values (phone/ID) are not addresses.
        if profile.digit_ratio > 0.6:
            return None
        if any(ch in MASK_CHARS for ch in v):   # masked identifiers, not addresses
            return None
        if "uuid" in profile.tags or "mac" in profile.tags or ("hex" in profile.tags and profile.length >= 16):
            return None
        if not (DETAIL_RE.search(v) or PRE_POSITION_RE.search(v) or ROMANISED_ROAD_RE.search(v)):
            return None
        # A number is only an address detail when it sits next to a road or building.
        if not (ROAD_RE.search(v) or BUILDING_RE.search(v)
                or PRE_POSITION_RE.search(v) or ROMANISED_ROAD_RE.search(v)):
            return None
        confidence = 0.9 if (ROAD_RE.search(v) and profile.hangul) else 0.72
        return Match(self.type_key, self.display_name, confidence,
                     evidence="도로명/건물/동·호수 상세 주소 패턴",
                     priority=self.priority, sensitivity=self.sensitivity)
