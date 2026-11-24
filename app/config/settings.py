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
    # Function A: columns the user designates as the record KEY. Their cells are
    # excluded from the privacy scan and reported as `KEY 제외`. Never inferred.
    key_columns: list[str] = field(default_factory=list)
    # Which analysis is running: "table" (Function A) or "metadata" (Function B).
    analysis_kind: str = "table"
    # How many cells the Cell-level AI may judge per file (reused verdicts count
    # as judged). Cells beyond the budget stay `미평가` - they are never silently
    # relabelled as non-personal.
    ai_context_reuse: bool = True
    # csv module field ceiling. A field longer than this is reported as a parse
    # error (counted in rows_error) rather than silently discarded. It is set
    # per file so JSON/Base64 columns are not rejected by the 131072 default.
    max_field_chars: int = 10_000_000
    # How many cells the GUI renders for one row at a time.
    gui_cell_page_size: int = 200
    # AI is never called per cell/row: only ambiguous rows, capped like this.
    max_ai_row_reviews: int = 100
    # AI column judgement is selective: columns already settled by the rule engine
    # are skipped unless ai_column_reviews="all". Counts are audited per file.
    max_ai_column_reviews: int = 200
    ai_column_reviews: str = "selective"  # selective | all | none
    # Cell-level AI review. "none" keeps the rule verdicts only; "selective" sends
    # the cells the rules could not settle (미평가/검토 필요) in batched Jev-Style
    # requests; "all" also re-checks rule-cleared cells. Cells the AI never judged
    # stay 미평가 - they are never relabelled as 비개인정보.
    max_ai_cell_reviews: int = 2000
    ai_cell_reviews: str = "selective"  # selective | all | none
    ai_cell_batch_size: int = 25  # questions per shared-state Jev-Style request
    threads: int = DEFAULT_THREADS
    use_ai: bool = True
    # AI 판정 engine: "jev" (Jev-Style Decision Model), "llm" (generative local GGUF), "auto"
    ai_engine: str = "auto"
    jev_model: str = "auto"  # auto | 0.8b-v3 | 2b-v4
    mode: str = "auto"  # auto | sampling | full
    resume: bool = False  # continue a cancelled run from its checkpoint row
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
