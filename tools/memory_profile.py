#!/usr/bin/env python3
"""Measure the memory a wide CSV costs in the rule engine.

Reports, for a synthetic N-column file:
  - RSS growth sampled while the columns are being scanned (psutil, 25 ms)
  - the per-column exact distinct set budget actually used
  - Python object memory traced with tracemalloc
  - the SQLite detail-store size

Usage:
    python tools/memory_profile.py --columns 100,1000,10000 --rows 200
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import threading
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config.settings import AnalysisConfig
from app.core.analysis_service import analyse_file
from app.core.column_stats import ColumnStats
from app.detectors.base import profile_of
from app.utils.hyperloglog import BoundedSet

try:
    import psutil
except ImportError:
    psutil = None


class SampledRSS:
    """Peak RSS over a sampling window (psutil gives current RSS, not peak)."""

    def __init__(self, interval: float = 0.025):
        self.interval = interval
        self.max_rss = 0.0
        self.samples = 0
        self.stop = threading.Event()
        self.thread = None

    def start(self) -> None:
        if not psutil:
            return
        proc = psutil.Process()

        def sample() -> None:
            while not self.stop.is_set():
                try:
                    rss = proc.memory_info().rss / (1024 ** 2)
                except Exception:
                    rss = 0.0
                self.samples += 1
                self.max_rss = max(self.max_rss, rss)
                self.stop.wait(self.interval)

        self.thread = threading.Thread(target=sample, daemon=True)
        self.thread.start()

    def finish(self) -> dict:
        if self.thread is not None:
            self.stop.set()
            self.thread.join(timeout=1.0)
        return {"peak_rss_mb": round(self.max_rss, 1), "rss_samples": self.samples}


def column_stats_cost(columns: int, rows: int, budget_total: int) -> dict:
    """Exact isolated measurement of ColumnStats + HLL growth."""
    per_column = max(20, budget_total // columns)
    stats = [
        ColumnStats(name=f"COL_{i:04d}", index=i, max_hit_rows=50,
                    distinct=BoundedSet(limit=per_column, p=10))
        for i in range(columns)
    ]
    tracemalloc.start()
    before = tracemalloc.get_traced_memory()[0]
    for row in range(rows):
        for column in stats:
            value = f"val_{row}_{column.index}"
            column.record(value, profile_of(value))
    after = tracemalloc.get_traced_memory()[0]
    tracemalloc.stop()
    hll_bytes = sum(len(column.distinct.hll.registers) for column in stats)
    exact = sum(column.distinct.exact_count for column in stats)
    return {
        "columns": columns,
        "rows": rows,
        "distinct_budget_per_column": per_column,
        "exact_values_kept": exact,
        "hll_register_bytes": hll_bytes,
        "traced_delta_mb": round((after - before) / 1e6, 1),
        "mb_per_column": round((after - before) / 1e6 / columns, 4),
    }


def build_csv(rows: int, columns: int, path: Path) -> None:
    header = ",".join(f"COL_{i:04d}" for i in range(columns))
    line = ",".join("ORD42" for _ in range(columns))
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(header + "\n")
        handle.write("\n".join(line for _ in range(rows)) + "\n")


def cap_probe(cap: int, rows: int, columns: int) -> dict:
    """Run one analysis in a fresh process so the sampled peak is not polluted."""
    code = (
        "import json, sys, time\n"
        "from app.config.settings import AnalysisConfig\n"
        "from app.core.analysis_service import analyse_file\n"
        "from tools.perf_matrix import PeakRSS, build_csv\n"
        "path, rows, cells = build_csv(int(sys.argv[1]), int(sys.argv[2]))\n"
        "peak = PeakRSS(); peak.start()\n"
        "started = time.time()\n"
        "result = analyse_file(path, AnalysisConfig(use_ai=False, max_detail_cells=int(sys.argv[3])))\n"
        "peak.stop.set(); peak.thread.join(timeout=1)\n"
        "counts = result.cell_counts\n"
        "result.detail_store.close()\n"
        "print(json.dumps({'max_detail_cells': int(sys.argv[3]), 'peak_rss_mb': round(peak.max_rss, 1), "
        "'rss_samples': peak.samples, 'cached_cells': counts.get('detail_cache_cells', 0), "
        "'stored_cells': counts['detail_cells_stored'], 'detail_store_mb': round(counts['detail_store_bytes']/1048576, 1), "
        "'seconds': round(time.time()-started, 1)}))\n"
    )
    completed = subprocess.run([sys.executable, "-c", code, str(rows), str(columns), str(cap)],
                               capture_output=True, text=True)
    if completed.returncode != 0:
        return {"max_detail_cells": cap, "error": completed.stderr.strip()[-300:]}
    return json.loads(completed.stdout)


def main() -> int:
    parser = argparse.ArgumentParser(description="Wide-CSV memory profile")
    parser.add_argument("--columns", default="100,1000,10000")
    parser.add_argument("--rows", type=int, default=200)
    parser.add_argument("--caps", default="",
                        help="comma separated max_detail_cells values for a per-process peak RSS probe")
    parser.add_argument("--cap-rows", type=int, default=1000)
    parser.add_argument("--cap-columns", type=int, default=10000)
    parser.add_argument("--json", default="")
    args = parser.parse_args()

    config = AnalysisConfig(use_ai=False)
    out = {"config": {"max_distinct_tracked": config.max_distinct_tracked,
                      "max_detail_cells": config.max_detail_cells,
                      "max_detail_rows": config.max_detail_rows},
           "measurement": "psutil RSS sampled every 25 ms + tracemalloc of ColumnStats",
           "scenarios": []}

    with tempfile.TemporaryDirectory() as tmp:
        for columns in (int(c) for c in args.columns.split(",")):
            path = Path(tmp) / f"wide-{columns}.csv"
            build_csv(args.rows, columns, path)
            monitor = SampledRSS()
            monitor.start()
            started = time.perf_counter()
            result = analyse_file(str(path), config)
            elapsed = time.perf_counter() - started
            rss = monitor.finish()
            store_bytes = result.cell_counts.get("detail_store_bytes", 0)
            record = {
                "columns": columns,
                "rows": args.rows,
                "cells_inspected": result.cell_counts["cells_inspected"],
                "seconds": round(elapsed, 2),
                **rss,
                "detail_store_mb": round(store_bytes / 1e6, 1),
                "isolated_column_stats": column_stats_cost(columns, args.rows, config.max_distinct_tracked),
            }
            print(json.dumps(record, ensure_ascii=False, indent=2))
            out["scenarios"].append(record)
            result.detail_store.close()

    if args.caps:
        out["detail_cell_caps"] = [
            cap_probe(int(cap), args.cap_rows, args.cap_columns)
            for cap in args.caps.split(",")
        ]
        for record in out["detail_cell_caps"]:
            print(json.dumps(record, ensure_ascii=False))

    if args.json:
        Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Written to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
