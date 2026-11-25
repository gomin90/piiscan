"""Version-managed privacy-type policy (한국 개인정보 유형 정책).

One source of truth for

* which privacy types are worth looking for,
* whether a type is 1순위 (first priority) or 2순위 (second priority),
* the column names that hint at the type,
* the intrinsic sensitivity of the type,
* how the type is detected (format / checksum / value shape / column context / AI).

`tier` is **detection priority only**. It is deliberately kept separate from
`risk_level`, `sensitivity` and `confidence`: a tier-1 hit is not automatically a
confirmed privacy cell, and a tier-2 hit is never dropped from the scan.

The engine, the name detector, the AI candidate selection, the risk engine and
Function B all read this file, so a policy change does not require code changes.
If the file is missing, unreadable or malformed, the built-in fallback policy is
used and the problem is reported in the diagnostics instead of crashing.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from app.config.settings import project_root

POLICY_FILENAME = "privacy_kr_2026.json"
REQUIRED_KEYS = (
    "display_name_ko", "display_name_en", "category", "tier", "identification",
    "method", "verdict", "sensitivity", "support",
)

#: Used when resources/rules/privacy_kr_2026.json is absent or invalid, so the
#: scanner still runs with a sane (documented) priority order.
FALLBACK_TYPES: dict[str, dict] = {
    "RESIDENT_ID": {"display_name_ko": "주민등록번호", "display_name_en": "Resident registration number",
                    "category": "고유식별번호", "tier": 1, "identification": "direct",
                    "method": "regex+checksum", "verdict": "confirm", "sensitivity": 1.0,
                    "support": "full", "column_aliases": [], "positive_hints": [], "negative_hints": []},
    "CARD_NUMBER": {"display_name_ko": "카드번호", "display_name_en": "Card number",
                    "category": "금융정보", "tier": 1, "identification": "direct",
                    "method": "regex+luhn", "verdict": "confirm", "sensitivity": 0.95,
                    "support": "full", "column_aliases": [], "positive_hints": [], "negative_hints": []},
    "NAME": {"display_name_ko": "이름", "display_name_en": "Person name",
             "category": "고객·임직원 정보", "tier": 1, "identification": "combined",
             "method": "regex+context", "verdict": "review", "sensitivity": 0.7,
             "support": "full", "column_aliases": ["NAME"], "positive_hints": [], "negative_hints": []},
}

_TOKEN_SPLIT = re.compile(r"[^0-9A-Za-z가-힣]+")
_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])")


def column_tokens(column_name: str) -> list[str]:
    """Split a column name into comparable tokens (also handles CamelCase)."""
    raw = re.sub(_TOKEN_SPLIT, " ", (column_name or "").strip())
    out: list[str] = []
    for part in raw.split():
        for piece in _CAMEL.split(part):
            piece = piece.strip()
            if piece:
                out.append(piece.upper())
    return out


def _normalise_alias(alias: str) -> str:
    return (alias or "").strip().upper().replace("_", "")


@dataclass(frozen=True)
class PrivacyType:
    code: str
    display_name_ko: str
    display_name_en: str
    category: str
    tier: int
    identification: str
    method: str
    verdict: str
    sensitivity: float
    support: str
    column_aliases: tuple[str, ...] = ()
    positive_hints: tuple[str, ...] = ()
    negative_hints: tuple[str, ...] = ()
    risk_step_up: bool = False

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "display_name_ko": self.display_name_ko,
            "display_name_en": self.display_name_en,
            "category": self.category,
            "tier": self.tier,
            "identification": self.identification,
            "method": self.method,
            "verdict": self.verdict,
            "sensitivity": round(self.sensitivity, 3),
            "support": self.support,
            "column_aliases": list(self.column_aliases),
            "risk_step_up": self.risk_step_up,
        }


class PrivacyPolicy:
    def __init__(self, types: dict[str, PrivacyType], version: str, source: str,
                 warnings: list[str] | None = None):
        self.types = types
        self.version = version
        self.source = source
        self.warnings = warnings or []
        #: Column semantics depend only on the column name, and every cell of a
        #: column would otherwise re-tokenise and re-score the same name.
        self._token_cache: dict[str, tuple[str, ...]] = {}
        self._affinity_cache: dict[tuple[str, str], float] = {}

    # ---- lookup --------------------------------------------------------------
    def get(self, code: str) -> PrivacyType | None:
        if not code:
            return None
        return self.types.get(code.upper())

    def tier(self, code: str) -> int:
        """Detection priority: 1 or 2. Unknown types default to 2 (still scanned)."""
        found = self.get(code)
        return found.tier if found else 2

    def sensitivity(self, code: str, default: float = 0.5) -> float:
        found = self.get(code)
        return found.sensitivity if found else default

    def display_name(self, code: str) -> str:
        found = self.get(code)
        return found.display_name_ko if found else code

    def category(self, code: str) -> str:
        found = self.get(code)
        return found.category if found else "기타"

    def method(self, code: str) -> str:
        found = self.get(code)
        return found.method if found else "unknown"

    def support_status(self, code: str) -> str:
        found = self.get(code)
        return found.support if found else "none"

    def risk_step_up(self, code: str) -> bool:
        found = self.get(code)
        return bool(found and found.risk_step_up)

    # ---- column semantics --------------------------------------------------
    def aliases_for(self, code: str) -> tuple[str, ...]:
        found = self.get(code)
        return found.column_aliases if found else ()

    def _tokens(self, column_name: str) -> tuple[str, ...]:
        cached = self._token_cache.get(column_name)
        if cached is None:
            cached = tuple(column_tokens(column_name))
            self._token_cache[column_name] = cached
        return cached

    def column_affinity(self, code: str, column_name: str) -> float:
        """0.0 .. 0.8: how strongly a column name points at this privacy type."""
        key = (code, column_name)
        cached = self._affinity_cache.get(key)
        if cached is not None:
            return cached
        found = self.get(code)
        if not found or not found.column_aliases:
            self._affinity_cache[key] = 0.0
            return 0.0
        tokens = self._tokens(column_name)
        joined = re.sub(r"[^0-9A-Z가-힣]", "", (column_name or "").upper())
        norm_aliases = [n for n in (map(_normalise_alias, found.column_aliases)) if n]
        score = 0.0
        for norm in norm_aliases:
            if norm in tokens:
                score += 0.40
            elif len(norm) >= 4 and norm in joined:
                # Short aliases ("AP", "CI", "SN") only count as whole tokens, so
                # APP_USER is not a wireless-network column and CIRCLE is not CI.
                score += 0.25
        value = min(score, 0.8)
        self._affinity_cache[key] = value
        return value

    def column_excluded(self, code: str, column_name: str) -> bool:
        found = self.get(code)
        if not found:
            return False
        upper = (column_name or "").upper()
        return any(hint.upper() in upper for hint in found.negative_hints)

    def high_priority_columns(self, column_name: str) -> list[PrivacyType]:
        """Every policy type whose column aliases name this column.

        Used before the generic "looks like a number/hash/code" shortcuts, so a
        `PASSWORD`, `CI`, `CELL_ID` or `GAID` column is never cleared as a plain
        system code just because its value looks numeric or base64-ish.
        """
        hits = []
        for found in self.types.values():
            if not found.column_aliases:
                continue
            if self.column_affinity(found.code, column_name) > 0.0:
                hits.append(found)
        hits.sort(key=lambda item: (item.tier, -item.sensitivity))
        return hits

    def coverage(self) -> list[dict]:
        rows = []
        for found in sorted(self.types.values(), key=lambda t: (t.tier, t.code)):
            rows.append({
                "type": found.code,
                "display_name": found.display_name_ko,
                "category": found.category,
                "tier": found.tier,
                "method": found.method,
                "support": found.support,
                "verdict": found.verdict,
                "identification": found.identification,
                "sensitivity": round(found.sensitivity, 3),
                "risk_step_up": found.risk_step_up,
                "column_aliases": list(found.column_aliases),
            })
        return rows

    def as_dict(self) -> dict:
        return {
            "policy_version": self.version,
            "source": self.source,
            "warnings": self.warnings,
            "type_count": len(self.types),
            "tier1": sum(1 for t in self.types.values() if t.tier == 1),
            "tier2": sum(1 for t in self.types.values() if t.tier == 2),
            "types": {code: found.as_dict() for code, found in self.types.items()},
        }


def _parse_type(code: str, raw: dict, warnings: list[str]) -> PrivacyType | None:
    if not isinstance(raw, dict):
        warnings.append(f"type {code}: expected an object")
        return None
    missing = [key for key in REQUIRED_KEYS if key not in raw]
    if missing:
        warnings.append(f"type {code}: missing keys {missing}")
        return None
    try:
        tier = int(raw.get("tier", 2))
    except (TypeError, ValueError):
        warnings.append(f"type {code}: tier must be 1 or 2")
        tier = 2
    if tier not in (1, 2):
        warnings.append(f"type {code}: tier {tier} is not 1 or 2, using 2")
        tier = 2
    try:
        sensitivity = float(raw.get("sensitivity", 0.5))
    except (TypeError, ValueError):
        warnings.append(f"type {code}: sensitivity must be a number")
        sensitivity = 0.5
    sensitivity = max(0.0, min(1.0, sensitivity))

    def _hints(key: str) -> tuple[str, ...]:
        value = raw.get(key, ())
        if isinstance(value, (list, tuple)):
            return tuple(str(item).strip() for item in value if str(item).strip())
        warnings.append(f"type {code}: {key} must be a list")
        return ()

    return PrivacyType(
        code=code.upper(),
        display_name_ko=str(raw.get("display_name_ko", code)),
        display_name_en=str(raw.get("display_name_en", code)),
        category=str(raw.get("category", "기타")),
        tier=tier,
        identification=str(raw.get("identification", "combined")),
        method=str(raw.get("method", "unknown")),
        verdict=str(raw.get("verdict", "review")),
        sensitivity=sensitivity,
        support=str(raw.get("support", "none")),
        column_aliases=_hints("column_aliases"),
        positive_hints=_hints("positive_hints"),
        negative_hints=_hints("negative_hints"),
        risk_step_up=bool(raw.get("risk_step_up", False)),
    )


@lru_cache(maxsize=4)
def load_policy(path: str | None = None) -> PrivacyPolicy:
    """Load the policy file, falling back to the built-in policy on any error."""
    target = Path(path) if path else project_root() / "resources" / "rules" / POLICY_FILENAME
    warnings: list[str] = []
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(f"policy file unavailable ({exc}) - using built-in fallback")
        return PrivacyPolicy(
            {code: parsed for code, parsed in (
                (c, p) for c, p in ((c, _parse_type(c, r, warnings)) for c, r in FALLBACK_TYPES.items())
                if p is not None)},
            version="builtin-fallback",
            source="builtin",
            warnings=warnings,
        )
    if not isinstance(raw, dict) or not isinstance(raw.get("types"), dict) or not raw["types"]:
        warnings.append("policy file has no 'types' object - using built-in fallback")
        return PrivacyPolicy(
            {code: parsed for code, parsed in (
                (c, p) for c, p in ((c, _parse_type(c, r, warnings)) for c, r in FALLBACK_TYPES.items())
                if p is not None)},
            version="builtin-fallback",
            source="builtin",
            warnings=warnings,
        )
    types: dict[str, PrivacyType] = {}
    for code, definition in raw["types"].items():
        parsed = _parse_type(code, definition, warnings)
        if parsed is not None:
            types[parsed.code] = parsed
    if not types:
        warnings.append("policy file contained no valid types - using built-in fallback")
        types = {code: parsed for code, parsed in (
            (c, p) for c, p in ((c, _parse_type(c, r, warnings)) for c, r in FALLBACK_TYPES.items())
            if p is not None)}
    return PrivacyPolicy(types, version=str(raw.get("policy_version", "unknown")),
                        source=str(target), warnings=warnings)


def get_policy(path: str | None = None) -> PrivacyPolicy:
    return load_policy(path)
