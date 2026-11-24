"""Operation-scale benchmark for Function A (150k-row real-table workloads).

Runs the real pipeline over synthetic fixtures at the sizes the production
environment is expected to deliver and records every metric promised in
docs/two-mode-analysis-design.md §6, plus the invariants that make the numbers
meaningful:

  1. rows x columns - KEY columns == logical cells
  2. sum of status counts == logical cells
  3. privacy_cells CSV rows == logical cells
  4. privacy_records CSV rows == rows
  5. Function B writes no cell/record/group artifacts

Peak RSS is sampled every 50 ms during the run; instantaneous RSS is never
reported as a peak.

    python tools/scale_benchmark.py --rows 150000 --columns 20 50 100 200
    python tools/scale_benchmark.py --rows 150000 --columns 20 --json results/scale.json
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psutil

from app.config.settings import AnalysisConfig
from app.core.analysis_service import analyse_file
from app.export.hit_exporter import write_cell_records, write_record_records

SURNAMES = ["김", "이", "박", "정", "홍", "최", "조", "윤", "구", "백"]
GIVEN = ["길동", "민준", "서연", "도윤", "하은", "유나", "태현", "지민"]
TEMPLATES = ["ADDR", "BIRTH", "AMOUNT", "CNT", "NOTE", "EMPTY_COL", "SN", "STATUS", "EMAIL", "CARD"]


def column_names(count: int) -> list[str]:
    names = ["SEQ_NO", "RECORD_KEY", "CUSTOMER_NAME", "PHONE_NO"]
    while len(names) < count:
        names.append(f"{TEMPLATES[len(names) % len(TEMPLATES)]}_{len(names)}")
    return names[:count]


def synth_row(rng: random.Random, columns: list[str]) -> list[str]:
    row = []
    for column in columns:
        kind = column.split("_")[0]
        if kind in {"SEQ", "RECORD", "SN", "CNT"}:
            row.append(str(rng.randint(1, 10_000_000)))
        elif kind == "NAME":
            row.append(rng.choice(SURNAMES) + rng.choice(GIVEN))
        elif kind == "PHONE":
            row.append(f"010-{rng.randint(1000, 9999)}-{rng.randint(1000, 9999)}")
        elif kind == "EMAIL":
            row.append(f"{rng.choice(GIVEN)}.{rng.randint(1, 999)}@example.com")
        elif kind == "CARD":
            row.append(f"{rng.randint(1000, 9999)}-{rng.randint(1000, 9999)}-"
                       f"{rng.randint(1000, 9999)}-{rng.randint(1000, 9999)}")
        elif kind == "EMPTY":
            row.append("")
        elif kind == "NOTE":
            row.append(rng.choice(["담당자: " + rng.choice(SURNAMES) + rng.choice(GIVEN),
                                   "주문 처리 완료", "시스템 코드 A-12", "배송 완료"]))
        elif kind == "AMOUNT":
            row.append(f"{rng.randint(1, 999)}.{rng.randint(0, 99):02d}")
        elif kind == "BIRTH":
            row.append(f"{rng.randint(1960, 2005)}-{rng.randint(1, 12):02d}-{rng.randint(1, 31):02d}")
        else:
            row.append(rng.choice(["강남구 Sequoia-ro 121", "마포구길 304", "ORD-2024-001"]))
    return row


def write_fixture(path: Path, rows: int, columns: list[str], seed: int = 11) -> int:
    rng = random.Random(seed)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for _ in range(rows):
            writer.writerow(synth_row(rng, columns))
    return path.stat().st_size


def run_one(rows: int, column_count: int, key_columns: list[str], out_dir: Path,
              use_ai: bool = False, ai_limit: int | None = None) -> dict:
    columns = column_names(column_count)
    keys = list(dict.fromkeys(name for name in key_columns if name in columns))
    fixture = out_dir / f"bench_{rows}x{column_count}.csv"
    file_bytes = write_fixture(fixture, rows, columns)

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

    config = AnalysisConfig(batch_size=2000, key_columns=keys, use_ai=use_ai,
                            ai_cell_reviews="selective" if use_ai else "none",
                            max_ai_cell_reviews=ai_limit or AnalysisConfig().max_ai_cell_reviews,
                            table="BENCH", schema="PROD", db_type="MySQL")
    started = time.perf_counter()
    result = analyse_file(fixture, config)
    analysis_seconds = time.perf_counter() - started

    store = result.detail_store
    counts = result.cell_counts
    completeness = store.verify_completeness()
    ai_audit = result.ai_audit or {}
    cell_csv = out_dir / f"bench_cells_{rows}x{column_count}.csv"
    record_csv = out_dir / f"bench_records_{rows}x{column_count}.csv"
    export_started = time.perf_counter()
    write_cell_records([result], cell_csv)
    cell_rows = write_cell_records.rows_written
    write_record_records([result], record_csv)
    record_rows = write_record_records.rows_written
    export_seconds = time.perf_counter() - export_started
    peak = max(peak, process.memory_info().rss)

    logical = completeness["logical_cells"]
    groups = store.group_count()
    sqlite_mb = store.path.stat().st_size / 1024 / 1024
    store.close()
    # The lifecycle module would reclaim this at process exit; cleaning up per run also
    # proves the store is released before the next configuration starts.
    store.cleanup()
    stop.set()
    sampler.join(1)
    peak = max(peak, process.memory_info().rss)

    metrics = {
        "rows": rows,
        "columns": column_count,
        "key_columns": keys,
        "total_cells": logical,
        "source_grid_cells": rows * column_count,
        "key_excluded_cells": counts.get("key_cells", 0),
        "analyzed_cells": counts.get("cells_inspected", 0),
        "non_empty_analyzed_cells": counts.get("cells_inspected", 0) - counts.get("empty_cells", 0),
        "empty_cells": counts.get("empty_cells", 0),
        "pii_confirmed": completeness["status_counts"].get("개인정보", 0),
        "review": completeness["status_counts"].get("검토 필요", 0),
        "non_pii": completeness["status_counts"].get("비개인정보", 0),
        "unrated": completeness["status_counts"].get("미평가", 0),
        "empty_status": completeness["status_counts"].get("빈 셀", 0),
        "key_status": completeness["status_counts"].get("KEY 제외", 0),
        "ai_cells": ai_audit.get("cell", {}).get("attempted", 0),
        "ai_reuses": ai_audit.get("cell", {}).get("reused", 0),
        "ai_seconds": ai_audit.get("cell", {}).get("seconds", 0.0),
        "ai_batches": ai_audit.get("cell", {}).get("batches", 0),
        "ai_status": getattr(result, "ai_summary", {}).get("status", ""),
        "pii_groups": groups,
        "total_seconds": round(analysis_seconds + export_seconds, 2),
        "analysis_seconds": round(analysis_seconds, 2),
        "export_seconds": round(export_seconds, 2),
        "cells_per_second": int(logical / analysis_seconds) if analysis_seconds else 0,
        "peak_rss_mb": round(peak / 1024 / 1024, 1),
        "sqlite_mb": round(sqlite_mb, 1),
        "report_csv_mb": round(cell_csv.stat().st_size / 1024 / 1024, 1),
        "input_csv_mb": round(file_bytes / 1024 / 1024, 1),
        "bytes_per_logical_cell": round(sqlite_mb * 1024 * 1024 / logical, 2) if logical else 0,
        "missing_cells": completeness["missing_cells"],
        "complete": completeness["complete"],
        "cell_csv_rows": cell_rows,
        "record_csv_rows": record_rows,
    }
    metrics["invariants"] = {
        "source_grid_equals_logical": rows * column_count == logical,
        "analyzed_cells_equal_logical_minus_key_excluded":
            metrics["analyzed_cells"] == logical - metrics["key_excluded_cells"],
        "status_counts_sum_equals_logical": sum(completeness["status_counts"].values()) == logical,
        "cell_csv_rows_equal_logical": cell_rows == logical,
        "record_csv_rows_equal_rows": record_rows == rows,
        "no_missing_cells": completeness["missing_cells"] == 0,
        "key_excluded_cells_equal_key_columns_times_rows":
            metrics["key_excluded_cells"] == len(keys) * rows,
    }
    metrics["all_invariants_pass"] = all(metrics["invariants"].values())
    return metrics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=150000)
    parser.add_argument("--columns", type=int, nargs="+", default=[20])
    parser.add_argument("--key", action="append", default=None,
                        help="KEY column(s) (repeatable). Defaults to SEQ_NO, RECORD_KEY")
    parser.add_argument("--out", default="results")
    parser.add_argument("--json", default="")
    parser.add_argument("--with-ai", action="store_true", help="run the selective cell-level Jev-Style pass as well")
    parser.add_argument("--ai-limit", type=int, default=None, help="max cells the AI may judge in the run")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(exist_ok=True)
    payloads = []
    for column_count in args.columns:
        key_label = ", ".join(args.key) if args.key else "none"
        print(f"=== {args.rows:,} rows x {column_count} columns "
              f"(KEY: {key_label}, AI {'selective' if args.with_ai else 'off'})")
        payload = run_one(args.rows, column_count, args.key or [], out_dir, use_ai=args.with_ai,
                           ai_limit=args.ai_limit)
        payloads.append(payload)
        for key in ("total_cells", "key_excluded_cells", "analyzed_cells", "non_empty_analyzed_cells",
                    "pii_confirmed",
                    "review", "non_pii", "unrated", "empty_status", "key_status",
                    "analysis_seconds", "cells_per_second", "peak_rss_mb", "sqlite_mb",
                    "report_csv_mb", "bytes_per_logical_cell", "pii_groups"):
            print(f"  {key:<24} {payload[key]}")
        print(f"  complete={payload['complete']} invariants={payload['invariants']}")
    if args.json:
        Path(args.json).write_text(json.dumps(payloads, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f" wrote {args.json}")
    return 0 if all(p["all_invariants_pass"] for p in payloads) else 1


if __name__ == "__main__":
    raise SystemExit(main())
