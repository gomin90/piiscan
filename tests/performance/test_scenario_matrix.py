"""QA performance matrix: wide and tall CSV shapes, rule vs AI inference.

Scenario | rows | columns
A        | 100  | 100
B        | 100  | 1,000
C        | 100  | 10,000
D        | 1,000 | 1,000
E        | 1,000 | 10,000   (skipped in the CI suite; run with tools/perf_matrix.py)
F        | 10,000 | 100

The AI numbers come from a deterministic FakeScorer so inference counts are
exact; the real Jev-Style Scorer is measured by tools/perf_matrix.py --ai all.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.config.settings import AnalysisConfig
from app.core.analysis_service import analyse_file

PII = "900101-123456-7"
SAFE = "ORD42-07"


class CountingScorer:
    def __init__(self):
        self.column_calls = 0
        self.row_calls = 0

    def load(self) -> bool:
        return True

    @property
    def llm(self):
        return "fake"

    def judge_column(self, column: dict) -> dict:
        self.column_calls += 1
        return {"column": column.get("column", ""), "privacy_type": "UNKNOWN",
                "jev_type": "PERSONAL_DATA", "confidence": 0.6, "reason": "fake",
                "requires_review": True, "personality": "PERSONAL", "decisions": 1}

    def judge_row(self, record: dict) -> dict:
        self.row_calls += 1
        return {"row": record.get("row"), "personality": "PERSONAL",
                "confidence": 0.6, "reason": "fake", "requires_review": True}

    def judge_code_group(self, group: dict):
        return None

    def memory_usage(self) -> dict:
        return {"rss_mb": 150.0, "peak_rss_mb": 150.0}

    def summary(self) -> dict:
        return {"engine": "fake-scorer", "status": "loaded",
                "column_calls": self.column_calls, "row_calls": self.row_calls}

    def close(self) -> None:
        pass


def build(tmp_path: Path, rows: int, columns: int) -> Path:
    path = tmp_path / f"{rows}x{columns}.csv"
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(",".join(f"COL_{i:04d}" for i in range(columns)) + "\n")
        for r in range(1, rows + 1):
            handle.write(",".join(PII if c % 5 == 0 else f"{SAFE}-{r % 97:02d}"
                                  for c in range(columns)) + "\n")
    return path


def rss_mb() -> float:
    try:
        import psutil
        return psutil.Process().memory_info().rss / (1024 ** 2)
    except ImportError:
        return 0.0


SCENARIOS = [("A", 100, 100), ("B", 100, 1_000), ("D", 1_000, 1_000), ("F", 10_000, 100)]
TIME_BUDGET = {"A": 5, "B": 10, "D": 30, "F": 60}


@pytest.mark.parametrize("name,rows,columns", SCENARIOS)
def test_scenario_matrix(tmp_path: Path, name: str, rows: int, columns: int):
    path = build(tmp_path, rows, columns)
    base_rss = rss_mb()
    scorer = CountingScorer()
    started = time.perf_counter()
    result = analyse_file(path, AnalysisConfig(use_ai=True, batch_size=1000), llm=scorer)
    total = time.perf_counter() - started
    counts = result.cell_counts

    expected_cells = rows * columns
    expected_hits = rows * ((columns + 4) // 5)  # every 5th column holds personal data
    assert counts["cells_inspected"] == expected_cells
    assert counts["detail_rows_stored"] == rows
    assert counts["detail_cells_stored"] == counts["cell_hits"] == expected_hits
    assert result.ai_audit["attempts"] == scorer.column_calls <= result.ai_audit["limit"]
    assert result.ai_audit["attempts"] + result.ai_audit["skipped"] == len(result.findings)
    assert result.ai_audit["row"]["attempts"] <= AnalysisConfig().max_ai_row_reviews
    assert total < TIME_BUDGET[name], f"{name} took {total:.1f}s (budget {TIME_BUDGET[name]}s)"

    # Memory must stay bounded for wide files.
    growth = rss_mb() - base_rss
    assert growth < 800, f"{name}: RSS grew {growth:.0f}MB"
    result.detail_store.close()


def test_ai_off_and_selective_ai_find_the_same_cells(tmp_path: Path):
    path = build(tmp_path, 500, 500)
    plain = analyse_file(path, AnalysisConfig(use_ai=False))
    scorer = CountingScorer()
    with_ai = analyse_file(path, AnalysisConfig(use_ai=True), llm=scorer)
    for key in ("cells_inspected", "cell_hits", "pii_cells", "review_cells"):
        assert plain.cell_counts[key] == with_ai.cell_counts[key]
    assert scorer.column_calls < len(with_ai.findings)
