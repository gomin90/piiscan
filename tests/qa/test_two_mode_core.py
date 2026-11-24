"""Function A core: KEY exclusion, row identity, cell-completeness invariant."""

from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
from pathlib import Path

from app.config.settings import AnalysisConfig
from app.core.analysis_service import analyse_file, merge_cell_verdict
from app.export.hit_exporter import iter_group_records, iter_record_records
from app.core.cell_verdict import (
    CODE_CLEARED_BASE, CODE_DETAIL, CODE_EMPTY, CODE_KEY, NON_PERSONAL_LABELS,
    STATUS_EMPTY, STATUS_KEY, STATUS_NON, STATUS_PII, STATUS_REVIEW, STATUS_UNRATED,
    code_to_evidence, code_to_status, status_to_code,
)


def _write(directory: Path, name: str, text: str) -> Path:
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


def analyse(path: Path, **overrides):
    """Run Function A with the AI disabled, closing the store deterministically."""
    config = AnalysisConfig(use_ai=False)
    for key, value in overrides.items():
        setattr(config, key, value)
    result = analyse_file(path, config)
    return result


def test_key_columns_are_excluded_but_still_counted():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(Path(tmp), "key.csv",
                      "MEMBER_ID,NAME,PHONE\n"
                      "1001,홍길동,010-1234-5678\n"
                      "1002,김철수,010-9876-5432\n")
        result = analyse(path, key_columns=["member_id"])

        counts = result.cell_counts
        store = result.detail_store
        assert counts["key_columns"] == 1
        assert counts["logical_cells"] == 6
        assert counts["key_cells"] == 2
        assert counts["rows_inspected"] == 2
        assert counts["cells_inspected"] == 4

        verify = store.verify_completeness()
        assert verify["complete"], verify
        assert verify["missing_cells"] == 0

        breakdown = store.status_breakdown()
        assert breakdown[STATUS_KEY] == 2
        assert breakdown[STATUS_PII] == 4
        assert sum(breakdown.values()) == counts["logical_cells"]

        key_cells = list(store.iter_cell_verdicts(STATUS_KEY, limit=10))
        assert len(key_cells) == 2
        for cell in key_cells:
            # KEY cells are never searched for personal data.
            assert cell["privacy_types"] == ""
            assert cell["masked_value"] == ""
            assert cell["cell_status"] == STATUS_KEY
            assert "KEY" in cell["decision_method"]


def test_deep_cell_pages_match_a_full_scan():
    """Keyset paging must return the same cells an OFFSET scan would, without walking them."""
    with tempfile.TemporaryDirectory() as tmp:
        rows = "\n".join(f"{row},ORD-{row},담당자: 박도윤" for row in range(1, 2001))
        path = _write(Path(tmp), "wide.csv", "SEQ_NO,ORDER,NOTE\n" + rows + "\n")
        result = analyse(path, key_columns=["SEQ_NO"])
        store = result.detail_store

        full = list(store.iter_cell_verdicts())
        assert len(full) == 2000 * 3

        for offset in (0, 1, 5, 2999, 5999, 5999 - 1, len(full) - 200, len(full) - 1):
            page = list(store.iter_cell_verdicts(offset=offset, limit=200))
            assert page == full[offset:offset + 200], f"page at offset {offset} differs"

        for status in (STATUS_PII, STATUS_KEY, STATUS_EMPTY, STATUS_NON, STATUS_UNRATED, STATUS_REVIEW):
            filtered = list(store.iter_cell_verdicts(status))
            for offset in (0, min(len(filtered) - 1, 300), max(len(filtered) - 200, 0)):
                page = list(store.iter_cell_verdicts(status, offset=offset, limit=200))
                assert page == filtered[offset:offset + 200], f"{status} page {offset} differs"
        store.cleanup()


def test_completeness_invariant_holds_for_ragged_rows():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(Path(tmp), "ragged.csv",
                      "A,B,C\n"
                      "1,2,3\n"
                      "4,5\n"
                      "6,7,8,9,10\n")
        result = analyse(path)
        store = result.detail_store

        verify = store.verify_completeness()
        assert verify["complete"], verify
        assert verify["logical_cells"] == 11
        # The widths the ledger is built from match the source rows.
        assert [row["total_cells"] for row in store.iter_row_verdicts(limit=10)] == [3, 3, 5]
        assert result.cell_counts["extra_cells"] == 2
        assert result.cell_counts["extra_columns"] == 2

        # Every cell of every row is answered exactly once.
        all_cells = list(store.iter_cell_verdicts(limit=50))
        positions = {(cell["row"], cell["column_index"]) for cell in all_cells}
        assert len(positions) == 11
        assert len(all_cells) == 11


def test_row_identity_is_the_physical_row_not_the_key_value():
    """Duplicate, empty and NULL KEY values must never merge source rows."""
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(Path(tmp), "dup_key.csv",
                      "ID,CUSTOMER,PHONE\n"
                      "A1,홍길동,010-1234-5678\n"
                      "A1,김철수,010-9876-5432\n"
                      ",박민준,010-1111-2222\n"
                      "A2,이영희,\n")
        result = analyse(path, key_columns=["ID"])
        store = result.detail_store
        assert store.row_count() == 4

        records = list(store.iter_record_keys())
        assert len(records) == 4
        assert len({record["row"] for record in records}) == 4
        assert sum(1 for record in records if record["key_defined"]) == 3

        conn = sqlite3.connect(store.path)
        fingerprints = [row[0] for row in conn.execute(
            "SELECT key_fingerprint FROM row_status WHERE analysis_id = ? ORDER BY row",
            (store.analysis_id,))]
        row_keys = [row[0] for row in conn.execute(
            "SELECT row_key FROM row_status WHERE analysis_id = ? ORDER BY row",
            (store.analysis_id,))]
        conn.close()

        assert len(fingerprints) == 4
        # Rows 1 and 2 share the KEY value "A1"; row 3 has no KEY value at all,
        # so its fingerprint is empty. None of that merges the rows.
        assert fingerprints[0] == fingerprints[1]
        assert fingerprints[2] == ""
        assert fingerprints[0] != fingerprints[3]
        assert len(set(row_keys)) == 4           # rows stay individually identified
        assert row_keys == ["r:1", "r:2", "r:3", "r:4"]
        # Raw personal data is never written to the store.
        assert store.path.read_bytes().find(b"01012345678") == -1


def test_reanalysing_a_file_replaces_its_own_records():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(Path(tmp), "same.csv", "NAME,PHONE\n홍길동,010-1234-5678\n")
        store_path = Path(tmp) / "shared.sqlite"
        analysis_id = ""
        for _ in range(2):
            config = AnalysisConfig(use_ai=False, detail_store_path=str(store_path))
            result = analyse_file(path, config)
            result.detail_store.close()
            analysis_id = result.analysis_id

        conn = sqlite3.connect(store_path)
        rows = conn.execute("SELECT COUNT(*) FROM row_status WHERE analysis_id = ?",
                            (analysis_id,)).fetchone()[0]
        cells = conn.execute("SELECT COUNT(*) FROM cell_info WHERE analysis_id = ?",
                             (analysis_id,)).fetchone()[0]
        runs = conn.execute("SELECT COUNT(*) FROM runs WHERE analysis_id = ?",
                            (analysis_id,)).fetchone()[0]
        conn.close()
        assert rows == 1 and cells == 2 and runs == 1


def test_status_codes_round_trip():
    # Derived statuses round-trip exactly; detail statuses share one byte and the
    # authoritative verdict lives in `cell_info`.
    for status in (STATUS_EMPTY, STATUS_KEY, STATUS_UNRATED):
        assert code_to_status(status_to_code(status)) == status
    for status in (STATUS_PII, STATUS_REVIEW):
        assert status_to_code(status) == CODE_DETAIL
    for label in NON_PERSONAL_LABELS:
        code = status_to_code(STATUS_NON, label)
        assert code_to_status(code) == STATUS_NON
        assert label in code_to_evidence(code)
    assert (CODE_EMPTY, CODE_KEY, CODE_DETAIL) == (0, 1, 2)
    assert code_to_status(CODE_CLEARED_BASE) == STATUS_NON


def test_rule_cleared_cells_do_not_store_a_value():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(Path(tmp), "cleared.csv",
                      "ORDER_ID,QTY,ORDER_DATE,PRICE,SKU\n"
                      "ORD-1,2,2024-01-01,19.99,SKU-A\n")
        result = analyse(path)
        store = result.detail_store
        cells = list(store.iter_cell_verdicts(limit=20))
        # Five cells, one verdict each: the rules cleared some outright and left
        # the rest for the model/reviewer.
        assert len(cells) == 5
        non_personal = [cell for cell in cells if cell["cell_status"] == STATUS_NON]
        assert non_personal
        assert all(cell["masked_value"] == "" for cell in non_personal)
        assert all(cell["evidence"] for cell in non_personal)
        assert all(cell["column"] for cell in cells)
        # The engine's counters and the ledger must agree cell by cell.
        breakdown = store.status_breakdown()
        assert breakdown[STATUS_PII] == result.cell_counts["pii_cells"]
        assert breakdown[STATUS_UNRATED] == result.cell_counts["unrated_cells"]


def test_multiple_files_share_a_store_without_mixing_rows():
    with tempfile.TemporaryDirectory() as tmp:
        first = _write(Path(tmp), "first.csv", "NAME,PHONE\n홍길동,010-1234-5678\n")
        second = _write(Path(tmp), "second.csv", "NAME,PHONE\n홍길동,010-1234-5678\n")
        store_path = Path(tmp) / "shared.sqlite"
        results = []
        for path in (first, second):
            config = AnalysisConfig(use_ai=False, detail_store_path=str(store_path))
            result = analyse_file(path, config)
            result.detail_store.close()
            results.append(result)

        conn = sqlite3.connect(store_path)
        for result in results:
            rows = conn.execute("SELECT COUNT(*) FROM row_status WHERE analysis_id = ?",
                                (result.analysis_id,)).fetchone()[0]
            cells = conn.execute("SELECT COUNT(*) FROM cell_info WHERE analysis_id = ?",
                                 (result.analysis_id,)).fetchone()[0]
            assert rows == 1 and cells == 2
        conn.close()

def test_identical_personal_values_group_without_raw_values():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(Path(tmp), "group.csv",
                      "ID,PHONE,CUSTOMER\n"
                      "1,010-1234-5678,홍길동\n"
                      "2,010-1234-5678,김철수\n"
                      "3,010-9999-8888,박민준\n"
                      "1,010-1234-5678,이영희\n")
        result = analyse(path, key_columns=["ID"])
        store = result.detail_store
        groups = list(store.iter_groups(limit=50))
        assert groups
        biggest = max(groups, key=lambda group: group["occurrences"])
        assert biggest["occurrences"] == 3
        assert biggest["distinct_rows"] == 3
        # Rows 1 and 4 share KEY "1" but are two records.
        # Rows 1 and 4 share the KEY value "1": three source rows map to two
        # distinct KEY records. Source rows are never merged by a shared KEY.
        assert biggest["distinct_records"] == 2
        assert len(biggest["key_samples"]) <= 10
        assert "PHONE" in biggest["columns"]
        # No raw personal data in the group table.
        assert store.path.read_bytes().find(b"01012345678") == -1
        total_grouped = sum(group["occurrences"] for group in groups)
        assert total_grouped == result.cell_counts["pii_cells"]


def test_record_and_group_reports_stream_without_raw_values():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(Path(tmp), "report.csv",
                      "ID,PHONE\n1,010-1234-5678\n2,010-1234-5678\n")
        result = analyse(path, key_columns=["ID"])
        records = list(iter_record_records([result]))
        groups = list(iter_group_records([result]))
        assert len(records) == 2
        assert len(groups) == 1
        assert groups[0]["occurrences"] == 2
        blob = "".join(json.dumps(record) for record in records + groups)
        assert "01012345678" not in blob
        assert "010-1234-5678" not in blob


def test_a_weak_ai_vote_never_confirms_a_cell_on_its_own():
    """The 3-option readout gives p~0.34 to a coin flip, so a low-confidence
    "personal" answer must not become a confirmed hit."""
    status, confidence, method, needs_review = merge_cell_verdict(STATUS_UNRATED, 0.0,
                                                                  "PERSONAL_DATA", 0.42)
    assert status == STATUS_REVIEW and needs_review
    assert confidence == 0.42 and method == "AI"

    status, _, method, needs_review = merge_cell_verdict(STATUS_UNRATED, 0.0,
                                                         "PERSONAL_DATA", 0.90)
    assert status == STATUS_PII and method == "AI" and not needs_review

    # AI can never erase a rule-confirmed hit, even when it disagrees.
    status, _, method, needs_review = merge_cell_verdict(STATUS_PII, 0.95,
                                                         "NON_PERSONAL_DATA", 0.99)
    assert status == STATUS_PII and method == "규칙" and needs_review

    # A model failure leaves the cell undecided rather than clean.
    status, _, _, needs_review = merge_cell_verdict(STATUS_UNRATED, 0.0, None, 0.0)
    assert status == STATUS_UNRATED and needs_review
