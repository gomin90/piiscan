#!/usr/bin/env python3
"""Performance matrix QA: rule scan vs AI inference across wide/tall CSV shapes.

Scenarios (rows x columns):
  A 100 x 100, B 100 x 1,000, C 100 x 10,000,
  D 1,000 x 1,000, E 1,000 x 10,000, F 10,000 x 100

For every scenario the tool reports:
  - cells the rule engine inspected, cell hits, distinct rows with hits
  - how much detail was persisted (SQLite) vs kept in the RAM cache
  - AI column/row judgement attempts, successes, failures and skips
  - rule time, AI time, total time, peak RSS, GPU/VRAM
  - cell-level and row-level precision / recall / F1 against the labelled fixture

Usage:
    python tools/perf_matrix.py --json results/perf_matrix.json
    python tools/perf_matrix.py --scenarios A,B --ai none
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.ai.gpu_detector import detect_hardware
from app.config.settings import AnalysisConfig
from app.core.analysis_service import analyse_file

try:
    import psutil
except ImportError:  # peak RSS then falls back to rusage
    psutil = None

SCENARIOS = {
    "A": (100, 100),
    "B": (100, 1_000),
    "C": (100, 10_000),
    "D": (1_000, 1_000),
    "E": (1_000, 10_000),
    "F": (10_000, 100),
}

# Values used in the fixture. Columns whose index is a multiple of 5 hold real
# personal data; every other column holds non-personal data that still looks
# numeric, so both false positives and false negatives are measurable.
PII_VALUE = "900101-123456-7"
SAFE_VALUE = "ORD42"


def build_csv(rows: int, columns: int) -> tuple[Path, set[int], set[tuple[int, int]]]:
    """Write a fixture CSV and return (path, labelled PII rows, labelled PII cells)."""
    header = ",".join(f"COL_{i:04d}" for i in range(columns))
    pii_rows: set[int] = set()
    pii_cells: set[tuple[int, int]] = set()
    lines = [header]
    for r in range(1, rows + 1):
        cells = []
        for c in range(columns):
            if c % 5 == 0:
                cells.append(PII_VALUE)
                pii_rows.add(r)
                pii_cells.add((r, c))
            else:
                cells.append(f"{SAFE_VALUE}-{r % 97:02d}")
        lines.append(",".join(cells))
    path = Path(tempfile.mkdtemp(prefix="piiscan-perf-")) / f"{rows}x{columns}.csv"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path, pii_rows, pii_cells


class CountingScorer:
    """Deterministic AI stand-in so the number of inferences is measurable."""

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


class PeakRSS:
    """Sampled peak resident memory.

    `psutil.Process().memory_info().rss` is the RSS *right now*, not the peak, so
    the peak is tracked by sampling it on a background thread and keeping the
    maximum. On platforms without psutil we fall back to the kernel's
    `ru_maxrss`, which is a true high-water mark but is per-process lifetime, so
    it is reported separately as an upper bound.

    Units: MiB (RSS bytes / 1024^2). Sample interval: 25 ms.
    """

    def __init__(self, interval: float = 0.025):
        self.interval = interval
        self.samples = 0
        self.max_rss = 0.0
        self.stop = threading.Event()
        self.thread = None

    def start(self) -> None:
        if not psutil:
            return
        proc = psutil.Process()

        def sample():
            while not self.stop.is_set():
                try:
                    rss = proc.memory_info().rss / (1024 ** 2)
                except Exception:
                    rss = 0.0
                self.samples += 1
                if rss > self.max_rss:
                    self.max_rss = rss
                self.stop.wait(self.interval)

        self.thread = threading.Thread(target=sample, daemon=True)
        self.thread.start()

    def finish(self) -> dict:
        if self.thread is not None:
            self.stop.set()
            self.thread.join(timeout=1.0)
        import resource

        rusage_max = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux reports KiB, macOS reports bytes.
        if sys.platform == "darwin":
            rusage_max /= 1024 ** 2
        else:
            rusage_max /= 1024.0
        return {
            "peak_rss_mb": round(self.max_rss, 1),
            "rss_samples": self.samples,
            "lifetime_max_rss_mb": round(rusage_max, 1),
            "measurement": "psutil RSS sampled every 25 ms (max over sampling window)",
        }


def prf(tp: int, fp: int, fn: int) -> dict:
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4),
            "tp": tp, "fp": fp, "fn": fn}


def run_scenario(name: str, rows: int, columns: int, mode: str) -> dict:
    path, labelled_rows, labelled_cells = build_csv(rows, columns)
    monitor = PeakRSS()
    monitor.start()
    started = time.perf_counter()

    review_mode = "selective" if mode == "real" else mode
    config = AnalysisConfig(use_ai=(mode != "none"), ai_column_reviews=review_mode, batch_size=1000)
    if mode == "none":
        scorer = None
    elif mode == "real":
        from app.ai import create_ai_engine
        scorer = create_ai_engine(config, detect_hardware())
    else:
        scorer = CountingScorer()
    result = analyse_file(path, config, llm=scorer)
    total_seconds = time.perf_counter() - started

    counts = result.cell_counts
    detected_cells = {(cell["row"], cell["column_index"]) for cell in result.detail_store.iter_cells()}
    detected_rows = {row["row"] for row in result.detail_store.iter_rows()}

    cell_metrics = prf(len(detected_cells & labelled_cells),
                       len(detected_cells - labelled_cells),
                       len(labelled_cells - detected_cells))
    row_metrics = prf(len(detected_rows & labelled_rows),
                      len(detected_rows - labelled_rows),
                      len(labelled_rows - detected_rows))

    hardware = detect_hardware()
    out = {
        "scenario": name,
        "rows": rows,
        "columns": columns,
        "ai_mode": mode,
        "rule_cells_inspected": counts["cells_inspected"],
        "cell_hits": counts["cell_hits"],
        "pii_cells": counts["pii_cells"],
        "review_cells": counts["review_cells"],
        "rows_with_hits": counts["rows_with_hits"],
        "detail_rows_stored": counts["detail_rows_stored"],
        "detail_cells_stored": counts["detail_cells_stored"],
        "detail_cache_rows": counts["detail_cache_rows"],
        "detail_cache_cells": counts["detail_cache_cells"],
        "detail_store_bytes": counts["detail_store_bytes"],
        "ai_column_attempts": result.ai_audit.get("attempts", 0),
        "ai_column_success": result.ai_audit.get("success", 0),
        "ai_column_failed": result.ai_audit.get("failed", 0),
        "ai_column_skipped": result.ai_audit.get("skipped", 0),
        "ai_row_attempts": result.ai_audit.get("row", {}).get("attempts", 0),
        "ai_row_success": result.ai_audit.get("row", {}).get("success", 0),
        "ai_row_failed": result.ai_audit.get("row", {}).get("failed", 0),
        "ai_row_skipped": result.ai_audit.get("row", {}).get("skipped", 0),
        "rule_seconds": round(result.timings.get("rules", 0.0), 3),
        "ai_seconds": round(result.ai_summary.get("inference_seconds", 0.0), 3),
        "total_seconds": round(total_seconds, 3),
        **monitor.finish(),
        "gpu_backend": hardware.backend,
        "gpu_vram_gb": hardware.vram_total_gb,
        "cell_metrics": cell_metrics,
        "row_metrics": row_metrics,
    }
    result.detail_store.close()
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Privacy Scanner performance matrix")
    parser.add_argument("--scenarios", default="A,B,C,D,E,F")
    parser.add_argument("--ai", choices=["none", "selective", "all", "real"], default="selective",
                        help="real uses the actual Jev-Style/llama.cpp scorer")
    parser.add_argument("--json", default="", help="write the matrix to a JSON file")
    args = parser.parse_args()

    hardware = detect_hardware()
    print(f"Hardware: {hardware.as_dict()}\n")
    rows_out = []
    for name in args.scenarios.split(","):
        name = name.strip().upper()
        rows, columns = SCENARIOS[name]
        print(f"Scenario {name}: {rows} rows x {columns} columns (AI mode {args.ai})")
        record = run_scenario(name, rows, columns, args.ai)
        rows_out.append(record)
        print(json.dumps(record, ensure_ascii=False, indent=2))
        print("-" * 60)

    if args.json:
        path = Path(args.json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"hardware": hardware.as_dict(), "scenarios": rows_out},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Matrix written to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
