"""QA for the v0.4.0 code-review follow-up work.

Every test here is executed against the real engine (no mocking of the rule
layer). The AI layer is exercised with a deterministic FakeScorer so the number
of model inferences is measurable; the real Scorer path is covered separately in
tests/qa/test_jev_ai.py and tests/performance/test_ai_memory_bounded.py.
"""
from __future__ import annotations

import csv
import itertools
import random
from pathlib import Path

from app.config.settings import AnalysisConfig
from app.core.analysis_service import analyse_file
from app.export.csv_exporter import write_csv
from app.export.hit_exporter import collect_hit_cells, collect_row_records, write_hit_rows


def write_csv_file(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


class FakeScorer:
    """Deterministic stand-in for the Jev-Style runtime that counts every call."""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.column_calls = 0
        self.row_calls = 0
        self.code_calls = 0
        self.column_states: list[dict] = []
        self.row_states: list[dict] = []

    def load(self) -> bool:
        return True

    @property
    def llm(self):
        return "fake"

    def judge_column(self, column: dict) -> dict | None:
        self.column_calls += 1
        self.column_states.append(column)
        if self.fail:
            return None
        return {
            "column": column.get("column", ""),
            "privacy_type": "UNKNOWN",
            "jev_type": "PERSONAL_DATA",
            "confidence": 0.61,
            "reason": "fake judgement",
            "requires_review": True,
            "personality": "PERSONAL",
            "decisions": 1,
        }

    def judge_row(self, record: dict) -> dict | None:
        self.row_calls += 1
        self.row_states.append(record)
        if self.fail:
            return None
        return {"row": record.get("row"), "personality": "PERSONAL",
                "confidence": 0.6, "reason": "fake", "requires_review": True}

    def judge_code_group(self, group: dict) -> dict | None:
        self.code_calls += 1
        return None

    def memory_usage(self) -> dict:
        return {"rss_mb": 120.0, "peak_rss_mb": 130.0}

    def summary(self) -> dict:
        return {"engine": "fake", "status": "loaded",
                "column_calls": self.column_calls, "row_calls": self.row_calls}

    def close(self) -> None:
        pass


# --------------------------------------------------------------------------- P1
def test_cell_verdicts_do_not_depend_on_row_order(tmp_path: Path):
    """Two identical values in the same column must get the same verdict.

    The old engine mixed a *prefix* support ratio into the cell decision, so the
    first match in a column was scored differently from the last one.
    """
    matched = "abc123def456"      # SN 후보: confidence 0.56, 판정은 컬럼 support에 따라 달라짐
    unmatched = [f"zz{i:04}" for i in range(30)]
    rows = unmatched + [f"{i},{matched}" for i in range(30, 100)]

    def verdicts(order: list[str]) -> dict[str, set[str]]:
        body = "IDX,VAL\n" + "\n".join(order)
        path = write_csv_file(tmp_path, "order.csv", body)
        result = analyse_file(path, AnalysisConfig(use_ai=False))
        out: dict[str, set[str]] = {}
        for cell in result.detail_store.iter_cells():
            out.setdefault(cell["masked_value"], set()).add(cell["decision"])
        return out

    original = verdicts(rows)
    shuffled = list(rows)
    random.Random(7).shuffle(shuffled)
    reordered = verdicts(shuffled)

    assert original == reordered, "row order changed the verdict of identical cells"
    for value, decisions in original.items():
        assert len(decisions) == 1, f"{value} got inconsistent verdicts: {decisions}"


def test_order_independence_keeps_counts_stable(tmp_path: Path):
    rows = [f"{i},{v}" for i, v in enumerate(
        ["900101-123456-7"] * 30 + ["zz001"] * 70, start=1)]
    body = "IDX,Payload\n" + "\n".join(rows)

    def counts(order: list[str]) -> dict:
        path = write_csv_file(tmp_path, "count.csv", body)
        result = analyse_file(path, AnalysisConfig(use_ai=False))
        counts = result.cell_counts
        return {k: counts[k] for k in ("cell_hits", "pii_cells", "review_cells", "ignored_cells")}

    first = counts(rows)
    alternate = list(reversed(rows))
    assert counts(alternate) == first, "reversing row order changed the detection counts"


def test_ragged_rows_keep_extra_fields_and_detect_pii(tmp_path: Path):
    body = (
        "CUST_NO,NAME,PHONE\n"
        "1,김민준,010-1234-5678\n"
        "2,이철수,010-2345-6789\n"
        "3,박지원\n"                                   # short row
        "4,정슬기,010-3456-7890\n"
        "5,왕영일,010-4567-8901\n"
        "6,홍길동,010-5678-9012,900101-123456-7,R12345678\n"   # fields past the header
        "7,오세훈,010-6789-0123,4111111111111111\n"
    )
    result = analyse_file(write_csv_file(tmp_path, "ragged.csv", body), AnalysisConfig(use_ai=False))
    assert result.rows_extra == 2
    assert result.rows_short == 1
    assert result.rows_irregular == 3
    assert result.cell_counts["extra_cells"] == 2

    columns = {cell["column"] for cell in result.detail_store.iter_cells()}
    assert any(column.startswith("EXTRA_COL") for column in columns), columns
    resident = [cell for cell in result.detail_store.iter_cells() if cell["type_key"] == "RESIDENT_ID"]
    assert resident, "resident number hidden in an extra field was not analysed"
    assert any(cell["column"].startswith("EXTRA_COL") or cell["column"] == "COL_004"
               for cell in resident), resident

    cells = collect_hit_cells([result])
    text = " ".join(c["masked_value"] for c in cells)
    assert "900101-1234567" not in text, "raw personal value reached the export"


def test_detail_store_preserves_every_hit_and_export_matches(tmp_path: Path):
    body = "IDX,EMAIL,NOTE\n" + "\n".join(
        f"{i},user{i}@example.com,note {i}" for i in range(1, 6001)
    )
    result = analyse_file(write_csv_file(tmp_path, "bulk.csv", body), AnalysisConfig(use_ai=False))
    counts = result.cell_counts
    assert counts["detail_rows_stored"] == 6000
    assert counts["detail_cache_rows"] == 5000  # memory cache only
    assert counts["detail_cells_stored"] >= 6000

    hit_path = write_hit_rows([result], tmp_path / "hits.csv")
    with open(hit_path, encoding="utf-8-sig", newline="") as handle:
        exported = list(csv.DictReader(handle))
    assert len(exported) == counts["detail_cells_stored"]
    assert {int(row["행번호"]) for row in exported} == set(range(1, 6001))

    rows_export = collect_row_records([result])
    assert len(rows_export) == counts["detail_rows_stored"]

    page = result.detail_store.iter_rows(offset=5000, limit=1000)
    assert [row["row"] for row in page] == list(range(5001, 6001))
    result.detail_store.close()


def test_confirmed_and_review_rows_are_reported_separately(tmp_path: Path):
    body = (
        "A,B,C\n"
        "1,11:22:33:44:55:66,zz001\n"          # confirmed direct identifier (MAC)
        "2,zz002,1506011234567\n"              # only an unconfirmed shape
        "3,zz003,zz004\n"                     # nothing detected at all
    )
    result = analyse_file(write_csv_file(tmp_path, "status.csv", body), AnalysisConfig(use_ai=False))
    rows = {row["row"]: row for row in result.detail_store.iter_rows()}
    assert rows[1]["status"] == "확정" and rows[1]["confirmed_pii"]
    assert rows[2]["status"] == "검토 필요" and not rows[2]["confirmed_pii"]
    assert rows[2]["possible_pii"]
    assert 3 not in rows, "a row with no cell evidence must not be reported as personal data"
    exported = collect_row_records([result])
    statuses = {int(r["row_number"]): r["status"] for r in exported}
    assert statuses[1] == "확정"
    assert statuses[2] == "검토 필요"


# --------------------------------------------------------------------------- P0
def test_ai_column_review_is_selective_and_audited(tmp_path: Path):
    body = "NAME,PHONE,EMAIL,AMOUNT,REF_CD,ADDRESS\n" + "\n".join(
        f"김민준{i},010-1234-{1000 + i},user{i}@example.com,{10000 + i},CD-{i},서울시 강남구 1{i}"
        for i in range(1, 61)
    )
    path = write_csv_file(tmp_path, "ai.csv", body)

    baseline = analyse_file(path, AnalysisConfig(use_ai=False))
    scorer = FakeScorer()
    selective = analyse_file(path, AnalysisConfig(use_ai=True), llm=scorer)
    audit = selective.ai_audit
    assert audit["policy"] == "selective"
    assert audit["attempts"] == scorer.column_calls
    assert audit["attempts"] <= audit["selected"]
    assert audit["selected"] + audit["skipped"] == len(selective.findings)
    assert audit["attempts"] < len(selective.findings), "columns already settled by rules still reached the AI"
    assert audit["success"] + audit["failed"] == audit["attempts"]

    all_mode = FakeScorer()
    analyse_file(path, AnalysisConfig(use_ai=True, ai_column_reviews="all"), llm=all_mode)
    assert all_mode.column_calls > scorer.column_calls

    none_mode = FakeScorer()
    no_ai = analyse_file(path, AnalysisConfig(use_ai=True, ai_column_reviews="none"), llm=none_mode)
    assert none_mode.column_calls == 0
    assert no_ai.ai_audit["skipped"] == len(no_ai.findings)

    # Selective AI must not lose any cell the rules found.
    for key in ("cells_inspected", "cell_hits", "pii_cells", "review_cells"):
        assert selective.cell_counts[key] == baseline.cell_counts[key], f"cell recall dropped with AI: {key}"

    # AI may not overwrite a rule verdict.
    for row in selective.results:
        if row.rule_decision:
            assert row.final_decision != ""


def test_row_ai_calls_are_capped_and_counted(tmp_path: Path):
    body = "IDX,Payload\n" + "\n".join(f"{i},abc123def45{i % 10}" for i in range(1, 201))
    path = write_csv_file(tmp_path, "rows.csv", body)
    scorer = FakeScorer()
    result = analyse_file(path, AnalysisConfig(use_ai=True, max_ai_row_reviews=10), llm=scorer)
    audit = result.ai_audit["row"]
    assert audit["attempts"] == scorer.row_calls <= 10
    assert audit["success"] + audit["failed"] == audit["attempts"]
    assert audit["candidates"] >= audit["attempts"]
    assert audit["skipped"] == audit["candidates"] - audit["attempts"]

    # The sample must not be limited to the first rows of the file.
    reviewed = {state["row"] for state in scorer.row_states}
    assert reviewed and max(reviewed) > 20


def test_row_ai_context_is_masked_and_risk_ordered(tmp_path: Path):
    body = (
        "Z_NAME,A_RESIDENT,B_PHONE,ZZ_NOTHING\n"
        "김민준,900101-123456-7,010-1234-5678,zz001\n"
    )
    scorer = FakeScorer()
    analyse_file(write_csv_file(tmp_path, "ctx.csv", body), AnalysisConfig(use_ai=True), llm=scorer)
    assert scorer.row_states, "row context was never sent to the AI"
    state = scorer.row_states[0]
    rendered = str(state)
    for raw in ("900101-123456-7", "010-1234-5678"):
        assert raw not in rendered, f"raw value {raw} reached the AI row context"
    cells = state["cells"]
    assert cells[0]["type"] in {"RESIDENT_ID", "PHONE_NUMBER", "NAME"}, cells
    assert state["confirmed_types"], "confirmed types must be part of the row context"
    assert len(cells) <= 12


# --------------------------------------------------------------------------- P2
def test_quoted_and_multiline_fields_are_parsed_consistently(tmp_path: Path):
    body = (
        'NAME,ADDRESS,NOTE\n'
        '"김,민준","서울시 강남구\n테헤란로 151","note, with , commas"\n'
        '"이철수","부산시 해운대구 중동 20","900101-1234567"\n'
    )
    path = write_csv_file(tmp_path, "quoted.csv", body)
    result = analyse_file(path, AnalysisConfig(use_ai=False))
    assert result.profile["column_count"] == 3
    assert result.rows_parsed == 2
    assert result.rows_irregular == 0
    assert result.rows_extra == 0
    columns = {cell["column"] for cell in result.detail_store.iter_cells()}
    assert "ADDRESS" in columns, "multiline quoted address was not scanned"
    assert result.rows_error == 0

    semicolon = "NAME;PHONE;EMAIL\n김;010-1234-5678;kim@example.com\n"
    pipe = "NAME|PHONE|EMAIL\n김|010-1234-5678;kim@example.com\n"
    tab = "NAME\tPHONE\tEMAIL\n김\t010-1234-5678\tkim@example.com\n"
    for name, text in (("semi.csv", semicolon), ("pipe.csv", pipe), ("tab.csv", tab)):
        res = analyse_file(write_csv_file(tmp_path, name, text), AnalysisConfig(use_ai=False))
        assert res.profile["column_count"] == 3, f"{name}: delimiter mis-detected"
        assert res.rows_parsed == 1


def test_wide_file_memory_is_bounded_and_progress_reports_cells(tmp_path: Path):
    columns = [f"COL_{i:05d}" for i in range(10_000)]
    body = ",".join(columns) + "\n" + "\n".join(
        ",".join("A" if (i % 7 == 0 and j % 200 == 0) else "12345678901234"
                 for j in range(len(columns)))
        for i in range(100)
    )
    from app.utils.logger import Diagnostics

    diagnostics = Diagnostics(enabled=True)
    seen: list[tuple[int, int, int]] = []
    result = analyse_file(
        write_csv_file(tmp_path, "wide.csv", body),
        AnalysisConfig(use_ai=False, batch_size=1000),
        progress=lambda rows, total, cells: seen.append((rows, total, cells)),
        diagnostics=diagnostics,
    )
    assert result.cell_counts["cells_inspected"] == 1_000_000
    assert seen and max(cells for _, _, cells in seen) >= 200_000, "progress must advance on cell throughput"
    budget = [event for event in diagnostics.events if event["event"] == "memory_budget"]
    assert budget and budget[0]["distinct_per_column"] <= 2000 // 10_000 + 20
    try:
        import psutil

        rss_mb = psutil.Process().memory_info().rss / (1024 ** 2)
        assert rss_mb < 900, f"wide file pushed RSS to {rss_mb:.0f}MB"
    except ImportError:
        pass


def test_exported_cells_are_formula_safe(tmp_path: Path):
    body = "=SUM(A1:B1),-ID,+NO,@REF,NORM\n" + "\n".join(
        f"900101-123456-7,1,2,3,{i}" for i in range(5)
    )
    result = analyse_file(
        write_csv_file(tmp_path, "inject.csv", body), AnalysisConfig(use_ai=False),
        overrides={"table": '=HYPERLINK("http://evil")', "schema": "-1+1", "db_type": "@cmd"},
    )
    cells = collect_hit_cells([result])
    assert cells, "no cell evidence to check"
    for cell in cells:
        assert not cell["masked_value"].startswith(("=", "+", "-", "@"))
    meta = [cell for cell in cells if cell["table"] or cell["schema"] or cell["db_type"]]
    for cell in meta:
        for value in (cell["table"], cell["schema"], cell["db_type"]):
            assert value == "" or value[0] != "=" or value.startswith("'"), value
    assert any(c["table"].startswith("'") for c in meta), "formula-like table name was not escaped"

    csv_path = write_csv([row.as_dict() for row in result.results], tmp_path / "flat.csv")
    with open(csv_path, encoding="utf-8-sig", newline="") as handle:
        lines = list(csv.reader(handle))
    for line in lines[1:]:
        for value in line:
            if value and value[0] in "=+-@":
                raise AssertionError(f"unescaped formula cell in report: {value!r}")
    result.detail_store.close()


def test_all_review_scenarios_still_run(tmp_path: Path):
    """Smoke test over the scenario shapes used in the performance QA."""
    for rows, columns in ((100, 100), (1000, 100)):
        body = ",".join(f"C{i:04d}" for i in range(columns)) + "\n" + "\n".join(
            ",".join("900101-123456-7" if j == 0 else str(r) for j in range(columns))
            for r in range(rows)
        )
        result = analyse_file(write_csv_file(tmp_path, f"s{rows}x{columns}.csv", body),
                              AnalysisConfig(use_ai=False))
        assert result.rows_parsed == rows
        assert result.cell_counts["cells_inspected"] == rows * columns
        assert result.cell_counts["detail_rows_stored"] == rows


# --------------------------------------------------------------------------- P0
def test_ai_optimisation_does_not_change_cell_detection(tmp_path: Path):
    """Selecting AI columns must not change what the rule engine found."""
    body = (
        "IDX,EMAIL,PHONE,SEQ,AMOUNT\n"
        "1,a@example.com,010-1234-5678,1001,12000\n"
        "2,b@example.com,010-2222-3333,1002,13000\n"
    )
    path = write_csv_file(tmp_path, "same.csv", body)
    baseline = analyse_file(path, AnalysisConfig(use_ai=False))
    selective = analyse_file(path, AnalysisConfig(use_ai=True, ai_column_reviews="selective"), llm=FakeScorer())
    assert selective.cell_counts["cell_hits"] == baseline.cell_counts["cell_hits"]
    assert selective.findings == baseline.findings

    # Columns already settled by rules are skipped by the selective policy.
    scorer = FakeScorer()
    selective_result = analyse_file(path, AnalysisConfig(use_ai=True, ai_column_reviews="selective"), llm=scorer)
    all_result = analyse_file(path, AnalysisConfig(use_ai=True, ai_column_reviews="all"), llm=FakeScorer())
    audit = selective_result.ai_audit
    assert audit["policy"] == "selective"
    assert audit["selected"] <= audit["attempts"] <= audit["limit"]
    assert all_result.ai_audit["selected"] >= audit["selected"]


def test_ai_missing_still_reports_rules(tmp_path: Path):
    """When the model cannot load, rule results and Row detail are intact."""
    body = "IDX,EMAIL\n1,a@example.com\n2,b@example.com\n"
    path = write_csv_file(tmp_path, "noai.csv", body)

    class UnavailableScorer(FakeScorer):
        def load(self) -> bool:
            return False

    result = analyse_file(path, AnalysisConfig(use_ai=True), llm=UnavailableScorer())
    assert result.ai_audit["attempts"] == 0
    assert result.ai_summary.get("status") in {"unavailable", "model_missing", "not_loaded"} or "status" in result.ai_summary
    keys = {finding["column"] for finding in result.findings}
    assert "EMAIL" in keys
    assert result.cell_counts["cell_hits"] == 2


def test_failed_ai_calls_are_counted(tmp_path: Path):
    body = "IDX,EMAIL\n" + "\n".join(f"{i},user{i}@example.com" for i in range(1, 30))
    path = write_csv_file(tmp_path, "fail.csv", body)
    scorer = FakeScorer(fail=True)
    result = analyse_file(path, AnalysisConfig(use_ai=True), llm=scorer)
    assert result.ai_audit["attempts"] == scorer.column_calls
    assert result.ai_audit["failed"] == result.ai_audit["attempts"]
    assert result.ai_audit["success"] == 0
    assert result.ai_audit["row"]["failed"] == result.ai_audit["row"]["attempts"]


# --------------------------------------------------------------------------- P2 security
def test_detector_evidence_contains_no_raw_privacy_text(tmp_path: Path):
    body = (
        "CARD,IMSI,RESIDENT,PHONE,EMAIL\n"
        "4111111111111111,310101010101010,900101-123456-7,010-1234-5678,a@example.com\n"
    )
    path = write_csv_file(tmp_path, "evidence.csv", body)
    result = analyse_file(path, AnalysisConfig(use_ai=False))
    rendered = str(result.findings) + str(collect_hit_cells([result]))
    for raw in ("4111111111111111", "310101010101010", "900101-123456-7", "010-1234-5678"):
        assert raw not in rendered, f"raw privacy value {raw} leaked into evidence"
    masked = {cell["masked_value"] for cell in collect_hit_cells([result])}
    assert all(len(value) <= 30 for value in masked)


def test_ai_does_not_overwrite_rule_verdicts(tmp_path: Path):
    """A column confirmed by rules keeps the rule decision even if the AI disagrees."""
    from app.core.result_aggregator import build_results, compare_decisions

    assert compare_decisions(["RESIDENT_ID"], {"privacy_type": "NONE", "confidence": 0.9})[
        "final_decision"] == "RESIDENT_ID"
    assert compare_decisions(["RESIDENT_ID"], {"privacy_type": "RESIDENT_ID", "confidence": 0.9})[
        "agreement"] == "일치"
    assert compare_decisions([], {"privacy_type": "PHONE_NUMBER", "confidence": 0.7})[
        "final_decision"] == "PHONE_NUMBER"

    body = "RESIDENT,AMOUNT\n900101-123456-7,12000\n900101-123456-7,13000\n"
    result = analyse_file(write_csv_file(tmp_path, "rule.csv", body),
                          AnalysisConfig(use_ai=True, ai_column_reviews="all"), llm=FakeScorer())
    rule_only = analyse_file(write_csv_file(tmp_path, "rule.csv", body), AnalysisConfig(use_ai=False))
    rule_columns = {r.column for r in rule_only.results}
    ai_columns = {r.column for r in result.results}
    assert rule_columns <= ai_columns, "AI mode dropped a rule-detected column"
    for row in result.results:
        if row.hits:  # rule evidence exists -> the rule decision wins
            assert row.final_decision == row.rule_decision, (row.column, row.final_decision, row.rule_decision)
            assert row.verdict != "형식 유사(무시)"
    # AI-only columns are advisory and always flagged for review.
    for row in result.results:
        if not row.hits:
            assert row.requires_review and row.method == "AI(보조)"
