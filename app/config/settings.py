"""Configuration for the scanner.

Defaults are read from resources/rules/*.json so that detection rules (including
user defined identifier patterns) can be tuned without touching code, which is
required for closed-network deployments.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_BATCH_SIZE = 1000
DEFAULT_CONTEXT_LENGTH = 2048
DEFAULT_MAX_DISTINCT_TRACKED = 2000
DEFAULT_MAX_SAMPLES = 6
DEFAULT_THREADS = 2
SUPPORTED_DELIMITERS = [",", ";", "\t", "|"]
SUPPORTED_ENCODINGS = ["utf-8", "utf-8-sig", "cp949", "euc-kr", "utf-16", "utf-16-le", "utf-16-be"]


@dataclass
class AnalysisConfig:
    batch_size: int = DEFAULT_BATCH_SIZE
    context_length: int = DEFAULT_CONTEXT_LENGTH
    max_distinct_tracked: int = DEFAULT_MAX_DISTINCT_TRACKED
    max_samples: int = DEFAULT_MAX_SAMPLES
    threads: int = DEFAULT_THREADS
    use_ai: bool = True
    mode: str = "auto"  # auto | sampling | full
    schema: str = ""
    table: str = ""
    db_type: str = ""
    header_row: int | None = None
    debug: bool = False
    custom_rules: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, rules_path: Path | None = None) -> "AnalysisConfig":
        config = cls()
        path = rules_path or project_root() / "resources" / "rules" / "custom_patterns.json"
        if path.exists():
            try:
                config.custom_rules = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                config.custom_rules = {}
        return config


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]
