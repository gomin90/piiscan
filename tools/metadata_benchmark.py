"""Function B (database metadata) benchmark at the operating scale of ~5,000 rows.

Measures the four things the design requires for Function B:

  * field-mapping accuracy over header variants a real extraction tool produces
  * column / table classification accuracy against a gold label set
  * AI batch (inference) count and time
  * wall-clock time and peak RSS

Gold labels are the two decidable classes only:
  pii      - the metadata plainly identifies personal data
  business - the metadata plainly identifies business/system data
Columns labelled "review" are intentionally left unlabelled: metadata alone
cannot settle them, so they are reported as unresolved rather than as an error.

    python tools/metadata_benchmark.py --rows 5000 --json results/metadata_bench.json
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psutil

from app.config.settings import AnalysisConfig
from app.metadata_analysis.field_mapping import detect_field_mapping
from app.metadata_analysis.metadata_service import analyse_metadata_file
from tests.fixtures.generate_metadata import generate_metadata

EXPECTED_ROLES = [
    {"table_name": "TABLE_NAME", "table_comment": "TABLE_COMMENT", "column_name": "COLUMN_NAME",
     "column_comment": "COLUMN_COMMENT", "data_type": "DATA_TYPE", "column_length": "COLUMN_LENGTH",
     "nullable": "NULLABLE", "primary_key": "PRIMARY_KEY", "foreign_key": "FOREIGN_KEY",
     "schema_name": "SCHEMA_NAME", "db_name": "DB_NAME"},
    {"table_name": "TBL_NAME", "table_comment": "TABLECOMMENTS", "column_name": "COL_NAME",
     "column_comment": "COLDESC", "data_type": "DTYPE", "column_length": "LEN",
     "nullable": "ISNULL", "primary_key": "PK", "foreign_key": "FK",
     "schema_name": "SCHEMA", "db_name": "DATABASE"},
    {"table_name": "테이블명", "table_comment": "테이블설명", "column_name": "컬럼명",
     "column_comment": "설명", "data_type": "자료형", "column_length": "길이",
     "nullable": "NULLABLE", "primary_key": "KEY", "foreign_key": "REF",
     "schema_name": "SCHEMA_NAME", "db_name": "DB_NAME"},
]

_LEGACY = {
    "table_name": "TBL_NAME",
    "table_comment": "TABLE_COMMENT",
    "column_name": "COL_NAME",
    "column_comment": "COL_DESC",
    "data_type": "DTYPE",
    "column_length": "LENGTH",
    "nullable": "NULLABLE",
    "primary_key": "PK",
    "foreign_key": "FK",
    "schema_name": "SCHEMA_NAME",
    "db_name": "DB_NAME",
}

# Gold labels for the fixture's columns. "pii" means the metadata is a personal
# data management target; "business" means it is not.
GOLD: dict[str, str] = {
    "CUST_NAME": "pii", "CUST_EMAIL": "pii", "MOBILE_NO": "pii", "ADDR_TEXT": "pii",
    "BIRTH_DATE": "pii", "REG_NO": "pii", "CARD_NO": "pii", "BANK_ACCT_NO": "pii",
    "EMP_NAME": "pii", "PHONE": "pii", "IMEI": "pii", "IMSI": "pii", "SSN_MASK": "pii",
    "CUST_NO": "business", "CUST_STATUS_CD": "business", "POINT_AMT": "business",
    "ORDER_ID": "business", "ORDER_DT": "business", "TOTAL_AMT": "business",
    "DELIVERY_STATUS": "business", "PAY_ID": "business", "EMP_NO": "business",
    "DEPT_CD": "business", "LOG_ID": "business", "EVENT_DT": "business",
    "SKU_CODE": "business", "PROD_ID": "business", "PROD_NAME": "business",
    "PRICE": "business", "STOCK_QTY": "business", "SHIP_ID": "business",
    "TRACK_NO": "business", "WAREHOUSE_CD": "business", "DEPART_DT": "business",
    "AUDIT_ID": "business", "USER_SYSTEM_ID": "business", "ACTION_CODE": "business",
    "CREATED_AT": "business", "IP_ADDR": "pii", "LAT": "pii", "LNG": "pii",
    "CC_HASH": "pii", "CARD_EXP_DT": "pii", "SHIP_ADDR_ID": "pii",
}

HEADER_VARIANTS = [
    ["TABLE_NAME", "TABLE_COMMENT", "COLUMN_NAME", "COLUMN_COMMENT", "DATA_TYPE",
     "COLUMN_LENGTH", "NULLABLE", "PRIMARY_KEY", "FOREIGN_KEY", "SCHEMA_NAME", "DB_NAME"],
    ["TBL_NAME", "TABLECOMMENTS", "COL_NAME", "COLDESC", "DTYPE", "LEN",
     "ISNULL", "PK", "FK", "SCHEMA", "DATABASE"],
    ["테이블명", "테이블설명", "컬럼명", "설명", "자료형", "길이",
     "NULLABLE", "KEY", "REF", "SCHEMA_NAME", "DB_NAME"],
]


def measure_case(path: Path, mapping: dict, label: str, use_ai: bool = False) -> dict:
    process = psutil.Process()
    peak = 0
    stop = threading.Event()

    def sample() -> None:
        nonlocal peak
        while not stop.is_set():
            peak = max(peak, process.memory_info().rss)
            stop.wait(0.05)

    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    config = AnalysisConfig(table="BENCH", schema="PROD", db_type="MySQL", use_ai=use_ai,
                            batch_size=1000)
    llm = None
    if use_ai:
        from app.ai.jev.engine import JevStyleEngine
        llm = JevStyleEngine(config)
    started = time.perf_counter()
    outcome = analyse_metadata_file(path, config, mapping=mapping, llm=llm)
    seconds = time.perf_counter() - started
    stop.set()
    sampler.join(1)
    peak = max(peak, process.memory_info().rss)
    store = outcome["store"]
    store.commit()
    total = sum(p.stat().st_size for p in (store.path, Path(str(store.path) + "-wal"),
                                          Path(str(store.path) + "-shm")) if p.exists())
    sqlite_mb = total / 1024 / 1024
    store.cleanup()

    result = outcome["result"]
    columns = result["columns"]
    labelled = [column for column in columns if column.column in GOLD]
    unlabelled = [column for column in columns if column.column not in GOLD]
    confusion: dict[str, dict[str, int]] = {}
    for column in labelled:
        bucket = confusion.setdefault(GOLD[column.column], {})
        bucket[column.verdict] = bucket.get(column.verdict, 0) + 1
    true_positive = confusion.get("pii", {}).get("개인정보 관련", 0)
    false_positive = confusion.get("business", {}).get("개인정보 관련", 0)
    false_negative = confusion.get("pii", {}).get("검토 필요", 0) + confusion.get("pii", {}).get("관련 낮음", 0)
    missed_as_review = confusion.get("business", {}).get("검토 필요", 0)
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    return {
        "case": label,
        "metadata_rows": result["rows_read"],
        "parse_error_rows": result["error_rows"],
        "skipped_rows": result["skipped_rows"],
        "columns": len(columns),
        "tables": len(result["tables"]),
        "columns_piirelevant": sum(1 for c in columns if c.verdict == "개인정보 관련"),
        "columns_review": sum(1 for c in columns if c.verdict == "검토 필요"),
        "columns_low": sum(1 for c in columns if c.verdict == "관련 낮음"),
        "labelled_columns": len(labelled),
        "unlabelled_columns": len(unlabelled),
        "confusion": confusion,
        "business_columns_over_flagged_as_review": missed_as_review,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "fp": false_positive,
        "fn": false_negative,
        "review_within_labelled": confusion.get("pii", {}).get("검토 필요", 0)
            + confusion.get("business", {}).get("검토 필요", 0),
        "ai": result["ai"],
        "rules_seconds": round(result["rules_seconds"], 3),
        "total_seconds": round(seconds, 3),
        "peak_rss_mb": round(peak / 1024 / 1024, 1),
        "sqlite_mb": round(sqlite_mb, 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=5000)
    parser.add_argument("--json", default="")
    parser.add_argument("--out", default="results")
    parser.add_argument("--with-ai", action="store_true", help="run the Jev-Style AI pass on metadata columns the rules could not settle")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(exist_ok=True)
    payload = []

    for index, headers in enumerate(HEADER_VARIANTS, start=1):
        fixture = generate_metadata(rows=args.rows, path=out_dir / f"metadata_bench_{index}.csv",
                                    headers=headers)
        detected = detect_field_mapping(headers)
        expected = EXPECTED_ROLES[index - 1]
        expected_hits = sum(1 for role, header in expected.items()
                            if detected.get(role, {}).get("header") == header)
        case = measure_case(fixture, detected, f"header variant {index}", use_ai=args.with_ai)
        case["mapping_fields"] = len(expected)
        case["mapping_correct"] = expected_hits
        case["mapping_accuracy"] = round(expected_hits / len(expected), 4)
        case["headers"] = headers
        payload.append(case)
        print(f"=== header variant {index}")
        for key in ("mapping_accuracy", "metadata_rows", "columns", "tables",
                    "precision", "recall", "f1", "fp", "fn", "confusion",
                    "total_seconds", "peak_rss_mb", "sqlite_mb", "ai"):
            print(f"  {key:<22} {case[key]}")

    if args.json:
        Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f" wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
