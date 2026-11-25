"""위도/경도 detector: 숫자 범위 + 소수점 분포 (두 컬럼 쌍 여부 후처리)."""
from __future__ import annotations

import re

from app.detectors.base import Detector, Match, ValueProfile

LAT_RE = re.compile(r"^-?(90(?:\.0+)?|[1-8]?\d(?:\.\d+))$")
LON_RE = re.compile(r"^-?(180(?:\.0+)?|1\d{2}(?:\.\d+)|[1-9]?\d(?:\.\d+))$")
DECIMAL_RE = re.compile(r"^-?\d{1,3}\.\d{3,}$")
MASK_DECIMAL_RE = re.compile(r"^-?\d{1,3}[*＊]{2,}$")
#: A single cell holding "37.5665,126.9780" (the common dumped-coordinate form).
PAIR_RE = re.compile(r"^(-?\d{1,3}\.\d+),(-?\d{1,3}\.\d+)$")


def _precision(value: str) -> int:
    if "." in value:
        return len(value.split(".")[1])
    return 0


class LocationDetector(Detector):
    """Coordinates are only confirmed with decimal precision or a column hint."""

    type_key = "LOCATION"
    display_name = "위치좌표"
    priority = 1
    sensitivity = 0.6
    candidate_tags = frozenset({"decimal", "digits", "dot_token", "long_text", "separated"})
    name_hints = ("LAT", "LNG", "LON", "GEO", "COORD", "POSITION", "GPS", "위도", "경도")

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        v = value.strip()
        hinted = any(hint in column_name.upper()
                      for hint in ("LAT", "LNG", "LON", "GEO", "COORD", "POSITION", "GPS", "위도", "경도"))
        pair = PAIR_RE.fullmatch(v.replace(" ", ""))
        if pair:
            lat, lon = pair.group(1), pair.group(2)
            if -90 <= float(lat) <= 90 and -180 <= float(lon) <= 180:
                return Match(self.type_key, "위치좌표(위도·경도)", 0.9,
                             evidence="위도/경도 쌍", fragment=v,
                             priority=self.priority, sensitivity=self.sensitivity,
                             match_start=value.find(lat), match_end=value.rfind(lon) + len(lon),
                             occurrence_index=1,
                             confirmed=hinted or _precision(lat) >= 4)
        if MASK_DECIMAL_RE.fullmatch(v):
            return Match(self.type_key, self.display_name, 0.6, masked=True,
                         evidence="마스킹된 좌표", priority=self.priority, sensitivity=self.sensitivity)
        # 힌트 없는 일반 소수는 좌표로 판정하지 않는다 (예: 3.1416 -> pi).
        if not DECIMAL_RE.fullmatch(v) and not hinted:
            return None
        if not hinted and _precision(v) < 5:
            return None
        try:
            number = float(v)
        except ValueError:
            return None
        precision = _precision(v)
        # Without a coordinate-like column name a bare decimal is only a *possible*
        # coordinate (it could be a rate, a price, pi), so it must never be
        # confirmed on surface form alone.
        hinted_only_confident = hinted
        if abs(number) <= 90 and LAT_RE.fullmatch(v):
            kind = "LATITUDE" if hinted and any(h in column_name.upper() for h in ("LAT", "위도")) else "LOCATION"
            base = 0.95 if precision >= 4 else (0.8 if hinted else 0.55)
            confidence = base if hinted_only_confident else min(base, 0.55)
            return Match(kind if kind != "LOCATION" else self.type_key,
                         "위도" if kind == "LATITUDE" else "위치좌표(위도 범위)",
                         confidence, evidence="위도 범위 -90..90",
                         priority=self.priority, sensitivity=self.sensitivity,
                         confirmed=hinted_only_confident)
        if abs(number) <= 180 and LON_RE.fullmatch(v) and precision >= 4:
            hinted_lon = hinted and any(h in column_name.upper() for h in ("LNG", "LON", "경도"))
            confidence = 0.95 if hinted_lon else 0.6
            return Match("LONGITUDE" if hinted_lon else self.type_key,
                         "경도" if hinted_lon else "위치좌표(경도 범위)",
                         confidence if hinted_only_confident else 0.55,
                         evidence="경도 범위 -180..180",
                         priority=self.priority, sensitivity=self.sensitivity,
                         confirmed=hinted_only_confident)
        return None
