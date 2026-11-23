"""QA for the v0.5.0 stabilisation round.

Every test runs against the real engine and the real SQLite store: the only
stub is the AI scorer, so the number of model calls is measurable.
"""
from __future__ import annotations

import csv
import json
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from app.config.settings import AnalysisConfig
from app.core.analysis_service import analyse_file, ai_column_candidates, select_column_candidates
from app.core.detail_store import DetailStore
from app.export.hit_exporter import (iter_hit_cells, iter_row_records, write_hit_rows, write_row_records)
from app.utils.csv_safety import formula_safe, looks_like_formula

try:
    import psutil
except ImportError:  # RSS assertions are skipped then
    psutil = None


def write_csv(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------- Row AI limit
class CountingScorer:
    """Deterministic AI stand-in that counts every inference it is asked for."""

    def __init__(self):
        self.row_calls = 0
        self.column_calls = 0

    def load(self):
        return True

    @property
    def llm(self):
        return "fake"

    def judge_column(self, column):
        self.column_calls += 1
        return {"column": column.get("column", ""), "privacy_type": "UNKNOWN",
                "jev_type": "PERSONAL_DATA", "confidence": 0.6, "reason": "fake",
                "requires_review": True, "personality": "PERSONAL", "decisions": 1}

    def judge_row(self, record):
        self.row_calls += 1
        return {"row": record.get("row"), "personality": "PERSONAL",
                "confidence": 0.6, "reason": "fake", "requires_review": True}

    def judge_code_group(self, group):
        return None

    def memory_usage(self):
        return {"rss_mb": 100.0}

    def summary(self):
        return {"engine": "fake", "status": "loaded", "decisions": self.row_calls + self.column_calls}

    def close(self):
        pass


@pytest.mark.parametrize("limit", [0, 1, 100, 1000, 10_000])
def test_row_review_limit_never_divides_by_zero(tmp_path: Path, limit: int) -> None:
    rows = "\n".join(["name,phone"] + [f"kim{r},010-1234-5678" for r in range(250)])
    path = write_csv(tmp_path, "rows.csv", rows)
    scorer = CountingScorer()
    result = analyse_file(path, AnalysisConfig(use_ai=True, max_ai_row_reviews=limit), llm=scorer)
    audit = result.ai_audit["row"]
    candidates = audit["candidates"]
    assert candidates > 0
    if limit == 0:
        assert audit["attempts"] == 0 and scorer.row_calls == 0
        assert audit["skipped"] == candidates
    else:
        assert audit["attempts"] == min(limit, candidates)
        assert audit["skipped"] == candidates - audit["attempts"]
        assert scorer.row_calls == audit["attempts"]
    assert not (limit == 0 and audit.get("zero_division"))


# ---------------------------------------------------------------- Row sampling
@pytest.mark.parametrize("total", [101, 150, 201, 299, 1000])
def test_row_ai_sample_represents_head_middle_tail(total: int) -> None:
    store = DetailStore(":memory:", "sample")
    for row in range(1, total + 1):
        store.conn.execute(
            "INSERT INTO row_detail (analysis_id, row, has_pii, status, confirmed_pii, "
            "possible_pii, column_names, column_indexes, cell_count, basis, confidence, "
            "risk_level, needs_review, ai_review, cells_checked) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (store.analysis_id, row, 1, "검토 필요", "[]", '["NAME"]', "[]", "[]", 1, "", 0.5,
             "낮음", 1, "{}", 1),
        )
    store.conn.commit()
    for limit in (10, 50, 100, total, total + 5):
        picked = [record["row"] for record in store.review_rows(limit)]
        expected = min(total, limit)
        assert len(picked) == expected
        assert picked[0] == 1
        assert picked[-1] == total
        # spread: no more than one full stride of unvisited rows in the sample
        stride = max(1, (total - 1) // (expected - 1)) if expected > 1 else total
        assert max(picked) - min(picked) >= min(total - 1, stride * (expected - 1) - 1)
        assert any(0.4 * total <= r <= 0.6 * total for r in picked)


def test_row_sample_is_deterministic(tmp_path: Path) -> None:
    rows = "\n".join(["name,phone"] + [f"kim{i},010-1234-5678" for i in range(201)])
    path = write_csv(tmp_path, "sample.csv", rows)
    runs = []
    for attempt in range(2):
        store = DetailStore(tmp_path / f"det-{attempt}.sqlite", "same-id")
        for row in range(1, 202):
            store.conn.execute(
                "INSERT INTO row_detail (analysis_id, row, has_pii, status, confirmed_pii, "
                "possible_pii, column_names, column_indexes, cell_count, basis, confidence, "
                "risk_level, needs_review, ai_review, cells_checked) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (store.analysis_id, row, 1, "검토 필요", "[]", "[]", "[]", "[]", 1, "", 0.4,
                 "낮음", 1, "{}", 2),
            )
        store.conn.commit()
        runs.append([r["row"] for r in store.review_rows(20)])
        store.close()
    assert len(runs[0]) == 20
    assert runs[0] == runs[1], "the same input must produce the same AI sample"


# ---------------------------------------------------------------- multi-file store
def test_three_files_same_row_numbers_share_one_store(tmp_path: Path) -> None:
    shared = tmp_path / "shared-detail.sqlite"
    for index in range(3):
        write_csv(tmp_path, f"file{index}.csv",
                  "name,phone\n" + "\n".join(f"user{index}_{r},010-1234-5678" for r in range(1, 6)))
    config = AnalysisConfig(use_ai=False, detail_store_path=str(shared))
    results = [analyse_file(tmp_path / f"file{index}.csv", config) for index in range(3)]
    for index, result in enumerate(results):
        stored = result.cell_counts["detail_rows_stored"]
        assert stored == 5, f"file{index} stored {stored} rows, expected 5"
        rows = [record["row"] for record in iter_row_records([result])]
        assert sorted(rows) == [1, 2, 3, 4, 5]
        cells = list(iter_hit_cells([result]))
        assert len(cells) == result.cell_counts["detail_cells_stored"] > 0
        assert all(cell["column"] in {"name", "phone"} for cell in cells)
    # Nothing from another file leaked into a single file's export.
    for index, result in enumerate(results):
        hits = list(iter_hit_cells([result]))
        assert len({hit["file"] for hit in hits}) == 1
        assert hits[0]["file"] == f"file{index}.csv"
    # Total across the shared database is the sum, not the max.
    store = DetailStore(shared, "audit")
    total = store.conn.execute("SELECT COUNT(*) FROM row_detail").fetchone()[0]
    assert total == 15
    store.close()


def test_shared_store_export_contains_only_own_file(tmp_path: Path) -> None:
    shared = tmp_path / "shared.sqlite"
    write_csv(tmp_path, "a.csv", "id,phone\n1,010-1234-5678\n2,010-2222-3333\n")
    write_csv(tmp_path, "b.csv", "id,phone\n1,010-5555-6666\n2,010-7777-8888\n")
    config = AnalysisConfig(use_ai=False, detail_store_path=str(shared))
    results = [analyse_file(tmp_path / "b.csv", config), analyse_file(tmp_path / "a.csv", config)]
    out = write_hit_rows(results, tmp_path / "hits.csv")
    with open(out, encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    files = {row["파일명"] for row in rows}
    assert files == {"a.csv", "b.csv"}
    per_file = {}
    for row in rows:
        per_file.setdefault(row["파일명"], set()).add(row["행번호"])
    assert per_file["a.csv"] == {"1", "2"}
    assert per_file["b.csv"] == {"1", "2"}
    stored = sum(r.cell_counts["detail_cells_stored"] for r in results)
    assert stored == len(rows)


# ---------------------------------------------------------------- streaming export
def test_large_export_is_streamed_and_exact(tmp_path: Path) -> None:
    """1,000,000 and 2,000,000 cell records export without materialising lists."""
    for total in (1_000_000, 2_000_000):
        rows = 10_000
        per_row = total // rows
        store = DetailStore(tmp_path / f"big-{total}.sqlite", "big")
        values = [(store.analysis_id, r, c, f"COL_{c}", "CARD_NUMBER", "카드번호", 0.9,
                   "4300-****-****-****", "rule:card", 1, 1, 0.9, 5, 0.2, 10, "개인정보", 0.9, 0, 0.5)
                  for r in range(1, rows + 1) for c in range(per_row)]
        store.conn.executemany(
            "INSERT INTO cell_detail (analysis_id, row, column_index, column, type_key, display_name, "
            "raw_confidence, masked_value, evidence, confirmed, masked, sensitivity, priority, "
            "name_hint, cells_checked, decision, confidence, needs_review, column_support) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            values,
        )
        row_values = [(store.analysis_id, r, 1, "확정", '["CARD_NUMBER"]', "[]", '["COL_0"]', "[0]",
                      per_row, "직접 식별", 0.9, "높음", 1, "{}", 10) for r in range(1, rows + 1)]
        store.conn.executemany(
            "INSERT INTO row_detail (analysis_id, row, has_pii, status, confirmed_pii, possible_pii, "
            "column_names, column_indexes, cell_count, basis, confidence, risk_level, needs_review, "
            "ai_review, cells_checked) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            row_values,
        )
        store.conn.commit()
        store.row_count = rows
        store.cell_count = total

        @dataclass
        class StubResult:
            path: str
            settings: dict
            detail_store: object
            row_records: list = field(default_factory=list)

        result = StubResult(path="big.csv",
                           settings={"analysed_at": "2026-01-01", "db_type": "", "schema": "", "table": "big"},
                           detail_store=store)

        from tools.perf_matrix import PeakRSS

        peak = PeakRSS(interval=0.01)
        peak.start()
        time.sleep(0.05)
        baseline = peak.max_rss
        started = time.perf_counter()
        hit_path = write_hit_rows([result], tmp_path / f"hits-{total}.csv")
        row_path = write_row_records([result], tmp_path / f"rows-{total}.csv")
        elapsed = time.perf_counter() - started
        peak.stop.set()
        peak.thread.join(timeout=1.0) if peak.thread else None
        growth_mb = peak.max_rss - baseline

        with open(hit_path, encoding="utf-8-sig") as handle:
            exported = sum(1 for _ in handle) - 1
        with open(row_path, encoding="utf-8-sig") as handle:
            exported_rows = sum(1 for _ in handle) - 1
        assert exported == total, f"exported {exported} cells, store has {total}"
        assert exported_rows == rows
        assert write_hit_rows.rows_written == total
        assert write_row_records.rows_written == rows
        if psutil:
            # Streaming keeps the export buffer at a few hundred KB during the
            # whole run, not gigabytes (peak, not end-of-run RSS).
            assert growth_mb < 40, f"export materialised the whole result set ({growth_mb:.1f} MB peak)"
        store.close()
        print(f"  {total:,} cells exported in {elapsed:.2f}s, peak RSS growth {growth_mb:.1f} MB")


# ---------------------------------------------------------------- GUI cell detail
def test_gui_cell_detail_is_capped_and_paged(tmp_path: Path) -> None:
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from app.ui.result_window import ResultWindow

    from app.ui.lifecycle import close_widgets

    QApplication.instance() or QApplication()

    columns = 1000
    header = ",".join(f"COL_{i:04d}" for i in range(columns))
    body = ",".join(["4300-0000-0000-0001"] * columns)
    path = write_csv(tmp_path, "wide.csv", f"{header}\n{body}\n")
    page = 300
    config = AnalysisConfig(use_ai=False, gui_cell_page_size=page)
    result = analyse_file(path, config)
    assert result.cell_counts["detail_cells_stored"] == columns
    window = ResultWindow(config)
    window.show_rows([result])
    assert window.row_table.rowCount() == 1
    window.select_row(window._row_page[0])
    assert window._cell_total() == columns
    assert window.hit_table.rowCount() == page, "the first render is capped at one cell page"
    for expected_page in range(1, 4):
        window._change_cell_page(1)
        expected = page if expected_page < 3 else columns - 3 * page
        assert window.hit_table.rowCount() == expected
        assert window._cell_page == expected_page
    window._change_cell_page(5)  # clamped to the last page
    assert window._cell_page == 3
    assert window.next_cell_page.isEnabled() is False
    window._change_cell_page(-1)
    assert window.prev_cell_page.isEnabled() is True


# ---------------------------------------------------------------- CSV large fields
def test_oversized_field_is_reported_not_silent(tmp_path: Path) -> None:
    payload = "A" * 300_000
    text = "id,note\n1," + payload + "\n2, 010-1234-5678\n"
    path = write_csv(tmp_path, "big-field.csv", text)
    config = AnalysisConfig(use_ai=False, max_field_chars=100_000)
    result = analyse_file(path, config)
    assert result.rows_error == 1
    assert result.rows_parsed == 1
    counts = result.as_dict()["row_analysis"]
    assert result.cell_counts["detail_store_path"]
    # The file is not reported as fully analysed.
    assert result.rows_error + result.rows_parsed == 2


def test_large_json_and_base64_fields_still_scanned(tmp_path: Path) -> None:
    import base64
    pii = "900101-1234567"
    blob = base64.b64encode(("name=" + pii).encode()).decode()
    json_blob = json.dumps({"payload": "x" * 200_000, "phone": "010-1234-5678",
                            "resident": pii})
    quoted = '"' + json_blob.replace('"', '""') + '"'
    path = write_csv(tmp_path, "json.csv", f"payload,encoded\n{quoted},{blob}\n")
    config = AnalysisConfig(use_ai=False, max_field_chars=500_000)
    result = analyse_file(path, config)
    assert result.rows_error == 0
    assert result.rows_parsed == 1
    types = {cell["privacy_type"] for cell in iter_hit_cells([result])}
    assert {"PHONE_NUMBER", "RESIDENT_ID"} <= types, types
    for cell in iter_hit_cells([result]):
        assert pii not in cell["masked_value"]


def test_header_parse_error_is_isolated(tmp_path: Path) -> None:
    # A header field beyond the stated limit must be reported, not abort analysis.
    path = write_csv(tmp_path, "broken.csv", "name," + "x" * 5000 + ",phone\nkim,short,010-1234-5678\n")
    config = AnalysisConfig(use_ai=False, max_field_chars=1000)
    result = analyse_file(path, config)
    assert result.rows_error >= 1
    assert result.parse_errors, "the parse failure must be recorded"
    assert result.rows_parsed >= 1, "the file is still analysed after a header failure"


# ---------------------------------------------------------------- counters / JSON
def test_extra_cells_counted_per_row(tmp_path: Path) -> None:
    # The header-derived width is 2; rows 6-8 carry a third field each.
    body = "\n".join(f"{i},ORD-{i}" for i in range(1, 6))
    extra = "\n".join(f"{i},ORD-{i},4300-0000-0001-{i:04d}" for i in range(6, 9))
    path = write_csv(tmp_path, "ragged.csv", f"a,b\n{body}\n{extra}\n")
    result = analyse_file(path, AnalysisConfig(use_ai=False))
    counts = result.cell_counts
    assert counts["extra_cells"] == 3, "every row's extra field must be counted, not just the first"
    assert counts["extra_columns"] == 1
    assert counts["detail_rows_stored"] == 3
    assert result.rows_extra == 3
    # The extra cells are actually scanned for personal data.
    hit_columns = {cell["column"] for cell in iter_hit_cells([result])}
    assert any(column.startswith("EXTRA_COL") for column in hit_columns)


def test_json_reports_detail_completeness(tmp_path: Path) -> None:
    rows = "\n".join(["name,phone"] + [f"kim{i},010-1234-5678" for i in range(120)])
    path = write_csv(tmp_path, "many.csv", rows)
    config = AnalysisConfig(use_ai=False, max_detail_rows=10)
    result = analyse_file(path, config)
    payload = result.as_dict()["row_analysis"]
    assert payload["detail_rows_stored"] == 120
    assert payload["detail_rows_in_json"] == 10
    assert payload["details_truncated"] is True
    assert payload["details_complete"] is False
    assert "detail_store_path" in payload
    assert payload["detail_store_path"].endswith(".sqlite")
    # JSON row count must equal the CSV export row count.
    rows_csv = write_row_records([result], tmp_path / "rows.csv")
    with open(rows_csv, encoding="utf-8-sig") as handle:
        assert sum(1 for _ in handle) - 1 == payload["detail_rows_stored"]


# ---------------------------------------------------------------- formula injection
@pytest.mark.parametrize("value", ["=1+1", " =1", "\t=1", "\r=1", "\n=1", "+cmd", "-2", "@x"])
def test_formula_injection_is_neutralised(value: str) -> None:
    safe = formula_safe(value)
    assert safe.startswith("'")
    assert safe[1:] == value, "the original identifier must not be trimmed"
    assert not looks_like_formula(safe)


@pytest.mark.parametrize("value", ["ORD-42", "  normal text", "1234", "이름"])
def test_normal_values_are_untouched(value: str) -> None:
    assert formula_safe(value) == value


# ---------------------------------------------------------------- store lifecycle
def test_temporary_store_is_cleaned_up(tmp_path: Path) -> None:
    store = DetailStore(tmp_path / "temp" / "detail.sqlite", "tmp-analysis")
    store.temp_dir = tmp_path / "temp"
    store.conn.execute("PRAGMA journal_mode=WAL")
    store.conn.execute("INSERT INTO row_detail (analysis_id, row, has_pii, status, confirmed_pii, "
                       "possible_pii, column_names, column_indexes, cell_count, basis, confidence, "
                       "risk_level, needs_review, ai_review, cells_checked) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (store.analysis_id, 1, 1, "확정", "[]", "[]", "[]", "[]", 0, "", 0.5, "낮음", 0, "{}", 1))
    store.conn.commit()
    assert (tmp_path / "temp" / "detail.sqlite-wal").exists()
    status = store.cleanup()
    assert status["temporary"] is True
    assert status["detail_store_removed"] is True
    assert not (tmp_path / "temp" / "detail.sqlite").exists()
    assert not (tmp_path / "temp" / "detail.sqlite-wal").exists()
    assert not (tmp_path / "temp" / "detail.sqlite-shm").exists()


def test_explicit_store_survives_cleanup(tmp_path: Path) -> None:
    path = write_csv(tmp_path, "keep.csv", "name,phone\nkim,010-1234-5678\n")
    store_path = tmp_path / "user-detail.sqlite"
    config = AnalysisConfig(use_ai=False, detail_store_path=str(store_path))
    result = analyse_file(path, config)
    status = result.detail_store.cleanup()
    assert status["temporary"] is False
    assert status["detail_store_removed"] is False
    assert store_path.exists()


def test_cli_closes_and_reports_temp_store(tmp_path: Path) -> None:
    path = write_csv(tmp_path, "cli.csv", "name,phone\nkim,010-1234-5678\n")
    config = AnalysisConfig(use_ai=False)
    result = analyse_file(path, config)
    store = result.detail_store
    status = store.cleanup()
    result.cell_counts["detail_store_cleanup"] = status["detail_store_removed"]
    assert status["detail_store_removed"] is True
    assert result.cell_counts["detail_store_cleanup"] is True


# ------------------------------------------------------------- progress / memory / leaks
def test_progress_advances_on_cell_throughput_for_wide_files(tmp_path: Path) -> None:
    """A 100 x 10,000 file has fewer than 5,000 rows but must still report progress."""
    columns = 10_000
    header = ",".join(f"COL_{i:04d}" for i in range(columns))
    body = "\n".join(",".join("ORD42" for _ in range(columns)) for _ in range(100))
    path = write_csv(tmp_path, "wide.csv", f"{header}\n{body}\n")
    calls: list[tuple[int, int, int]] = []
    analyse_file(path, AnalysisConfig(use_ai=False),
                 progress=lambda done, total, cells=0: calls.append((done, total, cells)))
    assert calls, "no progress was reported for a 1,000,000-cell file"
    assert max(cells for _done, _total, cells in calls) >= 200_000
    assert calls[-1][2] == 100 * columns


def test_ai_candidates_include_tail_and_weak_columns(tmp_path: Path) -> None:
    """Column order must not decide which columns the AI is allowed to look at."""
    columns = 1000
    header = ",".join(f"COL_{i:04d}" for i in range(columns))
    ambiguous_at = [3, 150, 300, 450, 600, 750, 900, 999]
    rows = []
    for _ in range(20):
        cells = ["ORD42"] * columns
        cells[7] = "900101-123456-7"          # settled: one strong, unopposed type
        for index in ambiguous_at:
            cells[index] = "4300-0000-0000-0000"   # card-shaped, rule confidence < 0.85
        rows.append(",".join(cells))
    path = write_csv(tmp_path, "tail.csv", f"{header}\n" + "\n".join(rows) + "\n")
    config = AnalysisConfig(use_ai=True, ai_column_reviews="selective", max_ai_column_reviews=5)
    result = analyse_file(path, config, llm=CountingScorer())
    candidates = ai_column_candidates(result.findings, config)
    candidate_columns = {c["finding"]["column"] for c in candidates}
    assert "COL_0999" in candidate_columns, "the right-most column was never a candidate"
    ordered = [c["finding"]["column"] for c in candidates]
    assert ordered == sorted(ordered, key=lambda name: -next(c["priority"] for c in candidates if c["finding"]["column"] == name))
    selected = {c["finding"]["column"] for c in select_column_candidates(candidates, 5)}
    assert "COL_0999" in selected, "the tail column dropped out of the selected set"
    assert len(selected) == 5

def test_conflicting_types_make_a_column_an_ai_candidate(tmp_path: Path) -> None:
    path = write_csv(tmp_path, "conflict.csv",
                     "AMOUNT\n" + "\n".join("4300-0000-0000-0001" for _ in range(50)) + "\n")
    config = AnalysisConfig(use_ai=True, ai_column_reviews="selective")
    result = analyse_file(path, config, llm=CountingScorer())
    found = [f for f in result.findings if f["column"] == "AMOUNT"]
    assert found
    candidates = ai_column_candidates(result.findings, config)
    assert any(c["finding"]["column"] == "AMOUNT" for c in candidates), (
        "a column with both a strong and a weak type was treated as settled")


def test_ai_candidate_selection_is_not_column_order_dependent(tmp_path: Path) -> None:
    columns = 900
    names_a = [f"COL_{i:04d}" for i in range(columns)]
    names_b = list(reversed(names_a))
    body_a = ["900101-123456-7"] + ["ORD42"] * (columns - 2) + ["123-45-6789"]
    body_b = list(reversed(body_a))
    path_a = write_csv(tmp_path, "order-a.csv", ",".join(names_a) + "\n" + ",".join(body_a) + "\n")
    path_b = write_csv(tmp_path, "order-b.csv", ",".join(names_b) + "\n" + ",".join(body_b) + "\n")
    config = AnalysisConfig(use_ai=True, ai_column_reviews="selective", max_ai_column_reviews=3)
    result_a = analyse_file(path_a, config, llm=CountingScorer())
    result_b = analyse_file(path_b, config, llm=CountingScorer())
    kinds_a = sorted({t for c in ai_column_candidates(result_a.findings, config) for t in c["types"]})
    kinds_b = sorted({t for c in ai_column_candidates(result_b.findings, config) for t in c["types"]})
    assert kinds_a == kinds_b, "reversing column order changed what the AI would see"
    assert result_a.cell_counts["cell_hits"] == result_b.cell_counts["cell_hits"]


def test_row_ai_audit_reports_candidates_selected_and_unreviewed(tmp_path: Path) -> None:
    body = "name\n" + "\n".join(f"kim{i}" for i in range(300))
    path = write_csv(tmp_path, "rows.csv", body + "\n")
    config = AnalysisConfig(use_ai=True, max_ai_row_reviews=20)
    result = analyse_file(path, config, llm=CountingScorer())
    audit = result.ai_audit["row"]
    total = result.detail_store.review_row_count()
    assert audit["candidates"] == total > 0
    assert audit["attempts"] == min(20, total)
    assert audit["skipped"] == total - audit["attempts"]


def test_detector_evidence_never_contains_raw_personal_values(tmp_path: Path) -> None:
    values = ["900101-123456-7", "010-1234-5678", "min.kim@example.com",
              "4300-0000-0000-0001", "1234567890123", "E12345678", "김민준"]
    path = write_csv(tmp_path, "leak.csv", ",".join(f"c{i}" for i in range(len(values))) + "\n"
                     + ",".join(values) + "\n")
    result = analyse_file(path, AnalysisConfig(use_ai=False))
    for cell in iter_hit_cells([result]):
        blob = (cell["masked_value"] + cell["evidence"] + cell["row_basis"] + cell["row_types"]
                + str(cell["column"]))
        for value in values:
            assert value not in blob, f"raw value reached evidence: {value} in {blob}"

def test_peak_rss_sampler_records_the_maximum_not_the_last_value() -> None:
    import time
    from tools.perf_matrix import PeakRSS

    peak = PeakRSS(interval=0.02)
    peak.start()
    time.sleep(0.1)
    baseline = peak.max_rss
    ballast = bytearray(60 * 1024 * 1024)   # held only in the middle of the window
    time.sleep(0.1)
    held = peak.max_rss
    del ballast
    time.sleep(0.1)
    report = peak.finish()
    assert held >= baseline + 50, "the peak while the ballast was live was not recorded"
    assert report["peak_rss_mb"] >= held - 0.1, "the sampler lost the maximum"
    assert report["rss_samples"] > 3, "the peak was not sampled repeatedly"
    assert "sampled" in report["measurement"]
    assert report["lifetime_max_rss_mb"] >= report["peak_rss_mb"]


def test_export_streams_without_loadinthe_store(tmp_path: Path) -> None:
    from tools.perf_matrix import PeakRSS

    path = write_csv(tmp_path, "wide-pii.csv", "IDX,NAME,PHONE\n" + "\n".join(
        f"{i},kim{i:05d},010-{i % 10000:04d}-{i % 10000:04d}" for i in range(20000)
    ) + "\n")
    result = analyse_file(path, AnalysisConfig(use_ai=False))
    peak = PeakRSS(interval=0.02)
    peak.start()
    import time
    time.sleep(0.05)
    before = peak.max_rss
    write_hit_rows([result], tmp_path / "cells.csv")
    peak.stop.wait(0.05)
    during = peak.max_rss
    peak.stop.set()
    growth = during - before
    stored = result.cell_counts["detail_cells_stored"]
    assert stored >= 40000
    assert growth < 40, f"export held the whole store in RAM ({growth:.1f} MB)"
    rows = Path(tmp_path / "cells.csv").read_text().count("\n") - 1
    assert rows == stored


def test_offscreen_field_limit_is_restored(tmp_path: Path) -> None:
    import csv as csv_module
    from app.core.csv_detector import read_preview

    path = write_csv(tmp_path, "limit.csv", "A,B\n1,2\n")
    original = csv_module.field_size_limit()
    rows = read_preview(path, "utf-8", ",", field_limit=1000)
    assert rows == [["A", "B"], ["1", "2"]]
    assert csv_module.field_size_limit() == original, "the process-wide field limit was left changed"
