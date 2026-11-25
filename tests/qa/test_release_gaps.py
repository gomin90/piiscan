"""QA: residual correctness gaps closed for v0.7.1.

Each test pins one requirement the spec stated that the code did not yet prove:

1. a Cell holding several occurrences reports every occurrence (offsets and
   count) together with its policy tier, instead of one collapsed hit;
2. KEY column values reach the model only in masked form;
3. Cell AI reuse is counted separately from real inference, and a reused Cell
   gets exactly the verdict of the Cell it was copied from;
4. a resumed run continues at the right physical source line, so already
   decided rows are neither re-analysed nor lost;
5. masked values are never cleared as non-personal by the shape short-cut.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.config.settings import AnalysisConfig
from app.core.analysis_service import analyse_file
from app.core.cell_verdict import rule_negative_verdict
from app.export.hit_exporter import collect_hit_cells


KEYED_TABLE = """SEQ_NO,RECORD_KEY,REMARK,PHONE
A-1001,K-2024-0001,"문의 010-1111-2222 / 010-3333-4444",010-1111-2222
A-1002,K-2024-0002,"no personal data here",010-5555-6666
""".strip()

KEY_SENSITIVE_TABLE = """SEQ_NO,SSN,CUSTOMER_ID
1,900101-1234567,A-1001
2,900102-2345678,A-1002
""".strip()

REPEATING_TABLE = """SEQ_NO,PHONE
1,010-1111-2222
2,010-1111-2222
3,010-1111-2222
4,010-2222-3333
""".strip()


class _RecordingLLM:
    """Stand-in model that records every payload it is handed."""

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.cell_calls = 0
        self.backend = "stub"
        self.load_seconds = 0.0
        self.llm = True

    def load(self) -> bool:
        return True

    def close(self) -> None:
        pass

    def summary(self) -> dict:
        return {"model": "stub", "backend": "stub", "decisions": self.cell_calls}

    def memory_usage(self) -> dict:
        return {"rss_mb": 10.0, "peak_rss_mb": 10.0}

    def _record(self, payload) -> None:
        self.prompts.append(json.dumps(payload, ensure_ascii=False))

    def judge_column(self, column: dict) -> dict:
        self._record(column)
        return {"column": column.get("column"), "privacy_type": "NONE",
                "confidence": 0.1, "reason": "stub", "requires_review": True}

    def judge_cells(self, column: dict, cells: list[dict]) -> list[dict]:
        self.cell_calls += 1
        self._record({"column": column, "cells": cells})
        return [{"decision": "검토 필요", "confidence": 0.45} for _ in cells]

    def judge_row(self, record: dict) -> dict:
        self._record(record)
        return {"personality": "알 수 없음", "confidence": 0.4, "reason": "stub"}

    def judge_code_group(self, group: dict) -> dict:
        self._record(group)
        return {}


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


# -------------------------------------------------------------- 1. occurrences
def test_cell_with_several_occurrences_reports_each_one(tmp_path: Path):
    result = analyse_file(_write(tmp_path / "t.csv", KEYED_TABLE),
                          AnalysisConfig(use_ai=False, key_columns=["SEQ_NO", "RECORD_KEY"]))
    cells = [c for c in collect_hit_cells([result]) if c["column"] == "REMARK"]
    assert len(cells) == 1, "one merged Cell per table Cell, not one per detector"
    cell = cells[0]
    assert cell["occurrence"] == 2, cell
    parts = cell["match_position"].split(";")
    assert len(parts) == 2, parts
    raw = "문의 010-1111-2222 / 010-3333-4444"
    for part in parts:
        start, end = (int(number) for number in part.split("@")[1].split("-"))
        assert raw[start:end] in ("010-1111-2222", "010-3333-4444"), (part, raw[start:end])
    assert cell["tier"] == 1, "전화번호 is a 1순위 type in the privacy policy"
    assert "*" in cell["masked_value"], "the report may not carry the raw Cell text"
    counts = result.cell_counts
    assert counts["multi_occurrence_cells"] == 1, counts
    assert counts["extra_occurrences"] == 1, counts
    # REMARK(row 1), PHONE(row 1), PHONE(row 2) carry hits; the other REMARK is
    # cleared as non-personal, so it is not a hit.
    assert counts["cell_hits"] == 3, counts
    assert counts["raw_cell_hits"] >= counts["cell_hits"], counts
    assert counts["verdict_complete"], counts


# ----------------------------------------------------- 2. KEY columns in prompts
def test_key_values_reach_the_model_masked_only(tmp_path: Path):
    llm = _RecordingLLM()
    analyse_file(_write(tmp_path / "keys.csv", KEY_SENSITIVE_TABLE),
                 AnalysisConfig(use_ai=True, key_columns=["SEQ_NO"], ai_cell_reviews="all"),
                 llm=llm)
    assert llm.prompts, "the AI path must actually have been exercised"
    joined = "\n".join(llm.prompts)
    for raw in ("900101-1234567", "900102-2345678", "9001011234567", "9001022345678"):
        assert raw not in joined, f"raw personal/KEY value inside an AI prompt: {raw}"
    assert "*" in joined, "prompt samples must be masked"


# ------------------------------------------------------ 3. AI reuse invariants
def test_identical_cells_reuse_one_inference(tmp_path: Path):
    llm = _RecordingLLM()
    result = analyse_file(_write(tmp_path / "reuse.csv", REPEATING_TABLE),
                          AnalysisConfig(use_ai=True, key_columns=["SEQ_NO"],
                                         ai_cell_reviews="all", ai_context_reuse=True,
                                         ai_cell_batch_size=1),
                          llm=llm)
    audit = result.ai_audit["cell"]
    assert audit["candidates"] == 4, audit
    assert audit["attempted"] == 2, "only the two distinct values are inferred"
    assert audit["reused"] == 2, audit
    assert llm.cell_calls == 2, "one scorer call per distinct Cell context"
    assert audit["attempted"] + audit["reused"] + audit["skipped"] == audit["candidates"], audit

    verdicts = {c["masked_value"]: c["verdict"] for c in collect_hit_cells([result])
                if c["column"] == "PHONE"}
    assert len(verdicts) == 2, verdicts


def test_reuse_can_be_disabled(tmp_path: Path):
    llm = _RecordingLLM()
    result = analyse_file(_write(tmp_path / "reuse.csv", REPEATING_TABLE),
                          AnalysisConfig(use_ai=True, key_columns=["SEQ_NO"],
                                         ai_cell_reviews="all", ai_context_reuse=False,
                                         ai_cell_batch_size=1),
                          llm=llm)
    audit = result.ai_audit["cell"]
    assert audit["attempted"] == 4 and audit["reused"] == 0, audit
    assert llm.cell_calls == 4, llm.cell_calls


# ------------------------------------------------- 4. resume at the right line
MULTILINE_TABLE = (
    "SEQ_NO,NOTE\n"
    '1,"quoted\nspanning two physical lines"\n'
    '2,"second row"\n'
    '3,"third row"\n'
    '4,"fourth row"\n'
)


def test_resume_keeps_physical_line_positions(tmp_path: Path):
    path = _write(tmp_path / "multi.csv", MULTILINE_TABLE)
    store_path = tmp_path / "detail.sqlite"
    seen = {"rows": 0}

    def stop_after_two() -> bool:
        seen["rows"] += 1
        return seen["rows"] > 2

    partial = analyse_file(path, AnalysisConfig(use_ai=False, resume=True,
                                                detail_store_path=str(store_path)),
                           should_stop=stop_after_two)
    assert partial.cancelled
    first_rows = {row for row, _, _ in partial.detail_store.iter_row_lines()}
    assert first_rows == {1, 2}, first_rows
    partial.detail_store.close()

    resumed = analyse_file(path, AnalysisConfig(use_ai=False, resume=True,
                                                detail_store_path=str(store_path)))
    store = resumed.detail_store
    lines = {row: (start, end) for row, start, end in store.iter_row_lines()}
    assert set(lines) == {1, 2, 3, 4}, lines
    assert store.resume_row() == 4, store.resume_row()
    # Row 1 occupies physical lines 2-3 of the file; the rest are single lines.
    assert lines[1] == (2, 3), lines[1]
    assert lines[2] == (4, 4), lines[2]
    assert lines[4] == (6, 6), lines[4]
    completeness = store.verify_completeness()
    assert completeness["complete"], completeness
    assert completeness["logical_cells"] == 4 * 2, completeness

    # Re-running a finished store must not duplicate rows.
    again = analyse_file(path, AnalysisConfig(use_ai=False, resume=True,
                                             detail_store_path=str(store_path)))
    assert {row for row, _, _ in again.detail_store.iter_row_lines()} == {1, 2, 3, 4}


# ------------------------------------------------------- 5. masked is never cleared
def test_masked_values_are_never_cleared_as_non_personal():
    for value, column in (
        ("12345******", "ACCOUNT_NO"),
        ("010-***-5678", "PHONE"),
        ("김**", "NAME"),
        ("********", "MASK_FIELD"),
        ("9001**-******", "RESIDENT_NO"),
    ):
        assert rule_negative_verdict(value, column) is None, f"{value!r} was cleared"
    # A genuinely non-personal shape in the same column is still cleared.
    assert rule_negative_verdict("42", "ACCOUNT_NO") is not None
