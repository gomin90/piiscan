"""사용자 정의 ID 패턴 detector.

규칙은 resources/rules/custom_patterns.json에서 정의한다. 접미사(suffix),
구분자(separator), 최대 길이(max_len), 문자 조합(charset)을 각각 관리할 수 있어
폐쇄망 환경에서도 코드 변경 없이 규칙만 교체할 수 있다.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from app.detectors.base import Detector, Match, ValueProfile
from app.config.settings import project_root

RULES_PATH = project_root() / "resources" / "rules" / "custom_patterns.json"

DEFAULT_RULES = [
    {
        "id": "sec_id",
        "label": ".sec로 끝나는 20자리 이하 ID",
        "suffix": ".sec",
        "max_len": 20,
        "charset": "alnum_-",
        "type": "CUSTOM_SEC_ID",
        "display_name": ".sec ID",
        "sensitivity": 0.6,
    },
    {
        "id": "id_suffix",
        "label": ".id로 끝나는 20자리 이하 ID",
        "suffix": ".id",
        "max_len": 20,
        "charset": "alnum_-",
        "type": "CUSTOM_ID_SUFFIX",
        "display_name": ".id ID",
        "sensitivity": 0.55,
    },
    {
        "id": "wildcard_dotted",
        "label": "%.%.% 형태의 20자리 이하 ID",
        "pattern": "%\\.%",
        "wildcard": True,
        "require_alpha": True,
        "max_len": 20,
        "charset": "alnum_-",
        "exclude_suffixes": [".pdf", ".csv", ".xlsx", ".xls", ".docx", ".txt", ".png", ".jpg",
                            ".jpeg", ".gif", ".mp4", ".zip", ".json", ".log", ".html", ".exe",
                            ".com", ".net", ".org", ".kr", ".co", ".jp", ".edu", ".gov", ".io"],
        "type": "CUSTOM_WILDCARD_ID",
        "display_name": "구분자 포함 ID",
        "sensitivity": 0.5,
    },
]

CHARSETS = {
    "alnum": r"[A-Za-z0-9]+",
    "alnum_": r"[A-Za-z0-9_]+",
    "alnum_-": r"[A-Za-z0-9_-]+",
    "digits": r"\d+",
    "any": r".+",
}


def build_regex(rule: dict) -> re.Pattern:
    """Compile a rule into a regex. '%' is treated as a wildcard token."""
    charset = CHARSETS.get(rule.get("charset", "alnum_-"), CHARSETS["alnum_-"])
    pattern = rule.get("pattern")
    if pattern:
        if rule.get("wildcard"):
            pattern = pattern.replace("%", charset)
        return re.compile(pattern)
    suffix = re.escape(rule.get("suffix", ""))
    separator = rule.get("separator")
    if separator:
        body = f"{charset}(?:{re.escape(separator)}{charset}){{1,}}"
    else:
        body = charset
    return re.compile(f"^(?:{body}){suffix}$")


def load_custom_rules(path: Path | None = None) -> list[dict]:
    """Read the shipped custom-ID rules; fall back to the built-in set."""
    target = path or RULES_PATH
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return list(DEFAULT_RULES)
    rules = raw.get("custom_rules") if isinstance(raw, dict) else None
    return list(rules) if rules else list(DEFAULT_RULES)


class CustomIdentifierDetector(Detector):
    type_key = "CUSTOM_IDENTIFIER"
    display_name = "사용자 정의 ID"
    priority = 1
    sensitivity = 0.5
    candidate_tags = frozenset({"latin", "digits", "dot_token", "alnum", "separated", "long_text", "blob"})
    name_hints = ()

    def __init__(self, rules: list[dict] | None = None):
        if rules is None:
            rules = load_custom_rules()
        self.rules = []
        # Flatten every rule once: the scan loop runs this detector on every Cell,
        # so per-Cell dict lookups and tuple rebuilding are pure overhead.
        for rule in rules:
            try:
                compiled = build_regex(rule)
            except re.error:
                continue
            self.rules.append((
                int(rule.get("max_len", 20)),
                bool(rule.get("require_alpha")),
                tuple(s.lower() for s in rule.get("exclude_suffixes", ())),
                compiled,
                rule.get("type", self.type_key),
                rule.get("display_name", self.display_name),
                rule.get("id", ""),
                float(rule.get("sensitivity", 0.5)),
            ))

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        v = value.strip()
        if not v or len(v) > 40:
            return None
        length = len(v)
        lower = None
        for max_len, require_alpha, excluded, regex, type_key, display, rule_id, sensitivity in self.rules:
            if length > max_len:
                continue
            if require_alpha and not profile.alpha:
                continue
            if excluded:
                if lower is None:
                    lower = v.lower()
                if lower.endswith(excluded):
                    continue
            if regex.fullmatch(v):
                return Match(type_key, display, 0.8, evidence=f"사용자 정의 규칙 {rule_id}",
                             priority=self.priority, sensitivity=sensitivity)
        return None
