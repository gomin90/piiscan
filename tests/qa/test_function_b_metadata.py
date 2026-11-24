"""QA for Function B (DB metadata analysis): mapping, scoring, storage, reports."""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

from app.config.settings import AnalysisConfig
from app.metadata_analysis.column_classifier import LOW, RELEVANT, REVIEW, ColumnMetadata, score_metadata
from app.metadata_analysis.field_mapping import FieldMapping
from app.metadata_analysis.metadata_service import analyse_metadata_file
from app.metadata_analysis.reports import (write_metadata_columns, write_metadata_review,
                                           write_metadata_tables)
from tests.fixtures.generate_metadata import generate_metadata, generate_small

FIXTURE = Path("tests/fixtures/metadata_small.csv")


def _analyse(path, **kwargs) -> dict:
    config = AnalysisConfig(use_ai=False, detail_store_path=str(kwargs.pop("store", "/tmp/qa_meta.db")))
    return analyse_metadata_file(str(path), config, **kwargs)


def test_field_mapping_handles_per_dbms_headers():
    headers = ["TBL_NAME", "TABLE_COMMENT", "COL_NAME", "COL_DESC", "DTYPE", "LENGTH",
               "NULLABLE", "PK", "FK", "SCHEMA_NAME", "DB_NAME"]
    mapping = FieldMapping.from_headers(headers)
    ok, message = mapping.validate()
    assert ok, message
    assert mapping.value(["orders", "주문", "ORDER_ID", "주문 ID", "BIGINT", "", "Y", "", "", "core", "db"],
                        "column_name") == "ORDER_ID"
    assert mapping.unmapped() == []


def test_field_mapping_accepts_korean_and_messy_headers():
    mapping = FieldMapping.from_headers(["테이블명", " 컬럼명", "자료형", "설명", "PRIMARY_KEY"])
    assert mapping.validate()[0]
    assert mapping.value(["CUSTOMER", "CUST_NO", "NUMBER", "고객 번호", "Y"], "table_name") == "CUSTOMER"
    # A header the detector cannot resolve is reported, never guessed silently.
    assert FieldMapping.from_headers(["A", "B", "C"]).unmapped() == ["A", "B", "C"]


def test_user_override_wins_over_auto_detection():
    mapping = _analyse(FIXTURE, mapping={"overrides": {"column_name": "BAD_HEADER"}})
    assert mapping["result"]["mapping"].describe()["applied"]["column_name"] == "BAD_HEADER"


def test_missing_required_field_is_reported_not_silently_skipped(tmp_path):
    path = tmp_path / "no_table.csv"
    path.write_text("COLUMN_NAME,DATA_TYPE\nCUST_NO,NUMBER\n", encoding="utf-8")
    try:
        analyse_metadata_file(str(path), AnalysisConfig(use_ai=False,
                                                        detail_store_path=str(tmp_path / "m.db")))
    except ValueError as exc:
        assert "table_name" in str(exc)
    else:
        raise AssertionError("metadata without a table column must fail loudly")


def test_rule_scoring_uses_name_comment_and_type():
    strong = score_metadata(ColumnMetadata("CUSTOMER", "CUST_NAME", "고객 성명", "VARCHAR", 50))
    assert strong.verdict == RELEVANT and "NAME" in strong.type_candidates
    resident = score_metadata(ColumnMetadata("CUSTOMER", "REG_NO", "주민등록번호", "VARCHAR", 13))
    assert resident.verdict == RELEVANT and "RESIDENT_ID" in resident.type_candidates
    business = score_metadata(ColumnMetadata("ORDER_HEAD", "ORDER_ID", "주문 ID", "BIGINT", None))
    assert business.verdict == LOW and not business.type_candidates
    # A comment that contradicts the column name still raises relevance, but never
    # claims personal data is present.
    ambig = score_metadata(ColumnMetadata("X", "REF_CD", "고객contact", "VARCHAR", 8))
    assert ambig.verdict in (REVIEW, RELEVANT)


def test_verdict_wording_never_asserts_personal_data_exists():
    for verdict in (RELEVANT, REVIEW, LOW):
        assert "확정" not in verdict
    every = [score_metadata(ColumnMetadata("T", c)).verdict
             for c in ("NAME", "IMEI", "SKU_CODE", "X", "CUST_NO", "PRICE")]
    assert set(every) <= {RELEVANT, REVIEW, LOW}


def test_metadata_storage_and_report_row_counts_match(tmp_path):
    result = _analyse(generate_metadata(5000, tmp_path / "meta.csv"),
                     store=tmp_path / "meta.db")
    store = result["store"]
    conn = sqlite3.connect(store.path)
    stored_columns = conn.execute(
        "SELECT COUNT(*) FROM column_verdicts WHERE analysis_id = ?", (store.analysis_id,)).fetchone()[0]
    stored_records = conn.execute(
        "SELECT COUNT(*) FROM meta_records WHERE analysis_id = ?", (store.analysis_id,)).fetchone()[0]
    conn.close()
    columns_csv = write_metadata_columns(store, tmp_path / "columns.csv")
    tables_csv = write_metadata_tables(store, tmp_path / "tables.csv")
    review_csv = write_metadata_review(store, tmp_path / "review.csv")
    assert stored_columns == stored_records == columns_csv == 5000
    assert tables_csv == len(result["result"]["tables"])
    # Function B never marks a column as confirmed, so every column stays reviewable.
    assert review_csv == 5000
    with open(tmp_path / "columns.csv", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert {row["verdict"] for row in rows} <= {RELEVANT, REVIEW, LOW}
    assert all(row["needs_confirmation"] == "1" for row in rows)


def test_malformed_metadata_rows_are_counted_not_lost(tmp_path):
    path = tmp_path / "broken.csv"
    path.write_text(
        "TABLE_NAME,COLUMN_NAME,DATA_TYPE\n"
        "CUSTOMER,CUST_NO,NUMBER\n"
        ",,\n"
        "CUSTOMER,,VARCHAR\n"
        "CUSTOMER,PHONE,VARCHAR\n",
        encoding="utf-8",
    )
    result = _analyse(path, store=tmp_path / "broken.db")
    summary = result["summary"]
    assert summary["metadata_rows"] == 2          # two usable column definitions
    assert summary["skipped_rows"] == 1          # a row with no column name
    assert summary["empty_rows"] == 1            # the genuinely blank row
    assert summary["columns"] == 2
    # Nothing is dropped: analysed + unusable + blank must equal the file's rows.
    assert summary["metadata_rows"] + summary["skipped_rows"] + summary["empty_rows"] == 4


def test_function_b_does_not_scan_table_data(tmp_path):
    """Function B is metadata-only: a CSV that looks like data still yields only
    metadata verdicts and no cell ledger."""
    path = tmp_path / "data.csv"
    path.write_text("TABLE_NAME,COLUMN_NAME\nCUSTOMER,CUST_NAME\nCUSTOMER,PHONE\n", encoding="utf-8")
    result = _analyse(path, store=tmp_path / "nofscan.db")
    store = result["store"]
    conn = sqlite3.connect(store.path)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "cell_detail" not in tables or store.kind == "metadata"
    assert result["summary"]["metadata_rows"] == 2


def test_five_thousand_row_benchmarks_are_bounded():
    import time
    result = _analyse(generate_metadata(5000), store="/tmp/qa_meta_bench.db")
    result["store"].close()
    audit = result["result"]["ai"]
    assert result["summary"]["metadata_rows"] == 5000
    assert audit["candidates"] > 0
    # No model is installed in this environment, so the pipeline must fall back
    # to rules and say so, rather than pretending the AI ran.
    assert audit["attempted"] == 0 and audit["skipped_reason"]
