"""암호화 / 마스킹 / 해시 컬럼 연관성 분석.

USER_NAME, USER_NAME_MASK, USER_NAME_HASH, USER_NAME_ENC 같은 컬럼 그룹이
같은 개인정보 원천에서 derive 되었는지, 그리고 실제로 보호 처리가 되었는지
값 패턴 기반으로 판단한다. 컬럼명만으로 확정하지 않는다.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from app.core.column_stats import ColumnStats

SUFFIX_MAP = {
    "MASK": "MASK",
    "MASKED": "MASK",
    "HASH": "HASH",
    "HASHED": "HASH",
    "SHA256": "HASH",
    "SHA512": "HASH",
    "MD5": "HASH",
    "ENC": "ENC",
    "ENCRYPT": "ENC",
    "ENCRYPTED": "ENC",
    "CRYPT": "ENC",
    "CIPHER": "ENC",
    "SEC": "SEC",
}

HEX_HASH_RE = re.compile(r"^[0-9a-fA-F]{32,64}$")
BASE64_RE = re.compile(r"^[A-Za-z0-9+/]{16,}={0,2}$")
MASK_CHAR_RE = re.compile(r"[*＊Xx]")


def split_suffix(name: str) -> tuple[str, str] | None:
    upper = name.upper()
    for suffix, kind in SUFFIX_MAP.items():
        if upper.endswith("_" + suffix):
            return upper[: -(len(suffix) + 1)], kind
    return None


def classify_protection(name: str, stats: ColumnStats) -> dict:
    """Describe what a *_MASK/_HASH/_ENC column actually contains."""
    kind = split_suffix(name)[1] if split_suffix(name) else None
    evidence = []
    verified = False
    non_empty = stats.non_empty or 0
    if non_empty == 0:
        return {"kind": kind or "UNKNOWN", "verified": False, "evidence": ["값 없음"]}

    hex_hits = stats.tags.get("hex", 0)
    blob_hits = stats.tags.get("blob", 0)
    masked_hits = stats.tags.get("masked", 0)

    if hex_hits >= non_empty * 0.8 and all(length in (32, 40, 64) for length in (stats.min_len, stats.max_len)):
        verified = True
        evidence.append(f"MD5/SHA 계열 hex 해시 ({stats.min_len}-{stats.max_len}자)")
    elif blob_hits >= non_empty * 0.5 and stats.max_len >= 16:
        verified = kind in ("ENC", "HASH")
        evidence.append("Base64 암호문/해시 blob")
    elif masked_hits >= non_empty * 0.6:
        verified = kind == "MASK"
        evidence.append("마스킹 문자 포함")
    elif kind == "HASH":
        evidence.append("해시 형식 불명확 - 추가 확인 필요")
    elif kind == "ENC":
        evidence.append("암호화 형식 불명확 - 추가 확인 필요")
    return {
        "kind": kind or "UNKNOWN",
        "verified": verified,
        "evidence": evidence or ["값 패턴 확인 필요"],
        "masked_ratio": round(masked_hits / non_empty, 3),
        "distinct_estimate": stats.distinct.estimate(),
    }


@dataclass
class ProtectionGroup:
    base: str = ""
    base_types: list[str] = field(default_factory=list)
    mask: dict | None = None
    hash_: dict | None = None
    enc: dict | None = None
    related: dict[str, dict] = field(default_factory=dict)
    status: str = "확인 필요"
    status_score: float = 0.0
    review_required: bool = True

    def as_dict(self) -> dict:
        return {
            "base": self.base,
            "base_types": self.base_types,
            "mask": self.mask,
            "hash": self.hash_,
            "enc": self.enc,
            "related": list(self.related),
            "status": self.status,
            "status_score": round(self.status_score, 2),
            "review_required": self.review_required,
        }


def analyze_protection(stats: list[ColumnStats], findings: list[dict]) -> list[ProtectionGroup]:
    by_name = {column.name: column for column in stats}
    finding_types = {finding["column"]: [t["display_name"] for t in finding["types"]] for finding in findings}

    groups: dict[str, ProtectionGroup] = defaultdict(ProtectionGroup)
    for column in stats:
        split = split_suffix(column.name)
        if split is None:
            continue
        base, kind = split
        if base not in by_name:
            # 원천 컬럼이 없는 보호 컬럼: 독립 보호 컬럼으로 기록
            group = groups.setdefault(f"__orphan__{column.name}", ProtectionGroup(column.name))
            group.related[column.name] = classify_protection(column.name, column)
            continue
        group = groups.setdefault(base, ProtectionGroup(base))
        detail = classify_protection(column.name, column)
        if kind == "MASK":
            group.mask = {"column": column.name, **detail}
        elif kind == "HASH":
            group.hash_ = {"column": column.name, **detail}
        elif kind == "ENC":
            group.enc = {"column": column.name, **detail}
        else:
            group.related[column.name] = detail

    result = []
    for base, group in groups.items():
        group.base_types = finding_types.get(base, [])
        present = sum(1 for part in (group.mask, group.hash_, group.enc) if part)
        verified = sum(1 for part in (group.mask, group.hash_, group.enc) if part and part.get("verified"))
        if present == 0:
            group.status = "보호 컬럼 없음"
            group.status_score = 0.0
            group.review_required = bool(group.base_types)
        elif present >= 3 and verified >= 3:
            group.status = "보호 확인 (높음)"
            group.status_score = 0.9
            group.review_required = False
        elif verified >= 1:
            group.status = "부분 보호 (추가 확인)"
            group.status_score = 0.5
            group.review_required = True
        else:
            group.status = "보호 의심 (값 검증 실패)"
            group.status_score = 0.2
            group.review_required = True
        result.append(group)
    return sorted(result, key=lambda g: g.status_score)
