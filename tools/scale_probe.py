"""Scale probe for the planned 150,000-row real-table workload.

Generates synthetic CSVs of the sizes the production environment is expected to
deliver (150k rows x 20/50/100/200 columns), runs the real rule engine over them
and reports measured throughput, RAM and SQLite space so the design can be sized
against facts rather than assumptions.

    python tools/scale_probe.py --rows 150000 --columns 20 50

No real personal data is used: every value is synthetic.
"""
from __future__ import annotations

import argparse
import csv
import random
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import AnalysisConfig
from app.core.analysis_service import analyse_file

SURNAMES = ["김", "이", "박", "정", "홍", "최", "조", "윤", "구", "백"]
GIVEN = ["길동", "민준", "서연", "도윤", "하은", "유나", "태현", "지민"]


def synthetic_row(rng: random.Random, index: int, columns: list[str]) -> list[str]:
    row = []
    for column in columns:
        kind = column.split("_")[0]
        if kind == "NAME":
            row.append(rng.choice(SURNAMES) + rng.choice(GIVEN))
        elif kind == "PHONE":
            row.append(f"010-{rng.randint(1000, 9999)}-{rng.randint(1000, 9999)}")
        elif kind == "SEQ" or kind == "CNT":
            row.append(str(rng.randint(1, 99999)))
        elif kind == "EMPTY":
            row.append("")
        elif kind == "NOTE":
            row.append(" ".join(rng.choice(["订单", "배송", "완료", "pending"]) for _ in range(3)))
        elif kind == "AMOUNT":
            row.append(f"{rng.randint(1, 999)}.{rng.randint(0, 99):02d}")
        else:
            row.append(f"{rng.randint(100000, 999999)}")
    return row


def column_names(count: int) -> list[str]:
    names = ["SEQ_NO", "CUSTOMER_NAME", "PHONE_NO"]
    pool = ["ADDR", "BIRTH", "AMOUNT", "CNT", "NOTE", "EMPTY_COL", "SN", "STATUS"]
    while len(names) < count:
        names.append(f"{pool[len(names) % len(pool)]}_{len(names)}")
    return names[:count]


def write_file(path: Path, rows: int, columns: list[str], seed: int = 7) -> int:
    rng = random.Random(seed)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for index in range(rows):
            writer.writerow(synthetic_row(rng, index, columns))
    return path.stat().st_size


def measure(path: str, rows: int, columns: int, ai: bool, key_columns: list[str]) -> dict:
    import psutil

    process = psutil.Process()
    config = AnalysisConfig(batch_size=1000, use_ai=ai, key_columns=key_columns)
    peak = 0

    def sample() -> None:
        nonlocal peak
        while not stop_sampling.is_set():
            peak = max(peak, process.memory_info().rss)
            stop_sampling.wait(0.2)

    import threading

    stop_sampling = threading.Event()
    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    started = time.perf_counter()
    result = analyse_file(path, config)
    elapsed = time.perf_counter() - started
    stop_sampling.set()
    sampler.join(2)
    counts = result.cell_counts
    store = result.detail_store
    db_bytes = store.path.stat().st_size if store and store.path.exists() else 0
    peak = max(peak, process.memory_info().rss)
    cells = counts.get("cells_inspected", 0)
    completeness = store.verify_completeness() if store else {}
    groups = store.build_groups() if store else 0
    return {
        "rows": rows,
        "columns": columns,
        "cells": cells,
        "seconds": round(elapsed, 2),
        "cells_per_second": int(cells / elapsed) if elapsed else 0,
        "peak_rss_mb": round(peak / 1024 / 1024, 1),
        "sqlite_mb": round(db_bytes / 1024 / 1024, 1),
        "logical_cells": counts.get("logical_cells", 0),
        "key_cells": counts.get("key_cells", 0),
        "complete": completeness.get("complete", False),
        "missing_cells": completeness.get("missing_cells", 0),
        "verdict_cells": counts.get("verdict_cells", 0),
        "pii_groups": groups,
        "status_counts": counts.get("verdict_status", {}),
        "file_mb": round(Path(path).stat().st_size / 1024 / 1024, 1),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Synthetic scale probe for 150k-row workloads")
    parser.add_argument("--rows", type=int, default=150_000)
    parser.add_argument("--columns", type=int, nargs="+", default=[20, 50])
    parser.add_argument("--ai", action="store_true")
    parser.add_argument("--key", nargs="*", default=["SEQ_NO"],
                       help="KEY columns to exclude from the privacy scan")
    parser.add_argument("--out-dir", default="results/scale")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = []
    for columns in args.columns:
        names = column_names(columns)
        path = out_dir / f"scale_{args.rows}x{columns}.csv"
        write_file(path, args.rows, names)
        data = measure(str(path), args.rows, columns, args.ai, args.key)
        data["file_mb"] = round(path.stat().st_size / 1024 / 1024, 1)
        report.append(data)
        print(f"{args.rows} x {columns} -> {data}")

    for data in report:
        cells = data["cells"]
        sqlite = data["sqlite_mb"]
        print(f"cells={cells:,} sqlite={sqlite} MB -> {sqlite * 1024 * 1024 / max(cells, 1):.1f} bytes/cell")

    print(f"peak_rss: {[d['peak_rss_mb'] for d in report]} MB")


if __name__ == "__main__":
    main()
