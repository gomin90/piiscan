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
    max_hit_rows: int = 50  # 컬럼별 행 단위 탐지 상세 retaining limit
    # Row-level detail is kept for every row that produced a cell hit, up to this
    # many rows (cell counts and row counts are always exact; only the detail list
    # is capped, and the truncation is reported in the result).
    max_detail_rows: int = 5000
    # Memory cache: how many cell records the in-memory row cache may hold in
    # total. Detail is written to SQLite unconditionally, so this only bounds RAM:
    # once the budget is reached, cached rows keep their verdict but not their
    # individual cell list (the GUI and exports read the cells from the store).
    max_detail_cells: int = 50_000
    # Where the detail database lives; empty means a temporary file per analysis.
    detail_store_path: str = ""
    # AI is never called per cell/row: only ambiguous rows, capped like this.
    max_ai_row_reviews: int = 100
    # AI column judgement is selective: columns already settled by the rule engine
    # are skipped unless ai_column_reviews="all". Counts are audited per file.
    max_ai_column_reviews: int = 200
    ai_column_reviews: str = "selective"  # selective | all | none
    threads: int = DEFAULT_THREADS
    use_ai: bool = True
    # AI 판정 engine: "jev" (Jev-Style Decision Model), "llm" (generative local GGUF), "auto"
    ai_engine: str = "auto"
    jev_model: str = "auto"  # auto | 0.8b-v3 | 2b-v4
    mode: str = "auto"  # auto | sampling | full
    sample_rows: int = 1000  # mode=sampling일 실제 분석 행 수
    gpu_layers: int | None = None  # None = auto (999 when a GPU backend is installed, 0 otherwise)
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
