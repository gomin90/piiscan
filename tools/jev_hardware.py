"""하드웨어/싥행 모드별 Jev-Style 실측 (CPU vs Metal, context 2K/4K/8K, RSS, latency).

`python tools/jev_hardware.py --models 0.8b-v3 2b-v4`
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import psutil

from app.ai.jev.decision import JevStyleDecisionRequest, PERSONALITY_OPTIONS
from app.ai.jev.model_manager import JevStyleModelManager
from app.ai.jev.adapter import JevStyleAdapter, PERSONALITY_QUESTION

SAMPLE_COLUMNS = [
    {"schema": "TB", "table": "MEMBER", "column": "USER_NM",
     "stats": {"rows": 50, "non_empty": 50, "null": 1, "distinct_estimate": 45,
               "samples": ["김길동", "이철수", "박영희", "최민준", "정수지"]}},
    {"schema": "TB", "table": "MEMBER", "column": "MOBILE",
     "stats": {"rows": 50, "non_empty": 50, "null": 0, "distinct_estimate": 50,
               "samples": ["01012345678", "01098765432", "01055551234"]}},
]


def rss_mb() -> float:
    return psutil.Process().memory_info().rss / (1024 ** 2)


def run(spec, backend: str, max_len: int, threads: int) -> dict:
    class Hardware:
        def __init__(self, backend):
            self.backend = backend
            self.available_ram_gb = 64.0

    adapter = JevStyleAdapter(spec, Hardware("metal" if backend == "metal" else "cpu"), threads=threads)
    adapter.max_len = max_len
    started = time.perf_counter()
    loaded = adapter.load()
    load_seconds = time.perf_counter() - started
    rss_after_load = rss_mb()
    child = getattr(adapter.engine, "proc", None)
    child_rss = rss_mb_of(child) if child else 0.0
    latencies = []
    failures = 0
    for column in SAMPLE_COLUMNS:
        from app.ai.jev.adapter import build_column_state
        state = build_column_state(column)
        request = JevStyleDecisionRequest("personality", state, PERSONALITY_QUESTION,
                                         PERSONALITY_OPTIONS, "choice", "general")
        t0 = time.perf_counter()
        try:
            result = adapter.decide(request)
            latencies.append(time.perf_counter() - t0)
        except Exception as exc:
            failures += 1
            print(f"  failure: {exc}")
    peak = max(adapter.peak_rss_mb, rss_after_load)
    adapter.close()
    return {
        "model": spec.key,
        "backend": backend,
        "max_len": max_len,
        "loaded": loaded,
        "load_seconds": round(load_seconds, 3),
        "parent_rss_after_load_mb": round(rss_after_load, 1),
        "total_peak_rss_mb": round(peak, 1),
        "scorer_rss_mb": round(child_rss, 1),
        "latency_ms": [round(value * 1000, 1) for value in latencies],
        "avg_latency_ms": round(sum(latencies) / len(latencies) * 1000, 1) if latencies else 0,
        "failures": failures,
        "error": adapter.last_error,
    }


def rss_mb_of(process) -> float:
    if process is None:
        return 0.0
    try:
        return psutil.Process(process.pid).memory_info().rss / (1024 ** 2)
    except Exception:
        return 0.0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="*", default=["0.8b-v3", "2b-v4"])
    parser.add_argument("--contexts", nargs="*", type=int, default=[2048, 4096, 8192])
    parser.add_argument("--output", default="results/qa/jev_hardware.json")
    args = parser.parse_args(argv)

    manager = JevStyleModelManager()
    available = {spec.key: spec for spec in manager.discover()}
    rows = []
    for key in args.models:
        spec = available.get(key)
        if spec is None:
            print(f"[skip] {key}: 모델 없음")
            continue
        for backend in ("metal", "cpu"):
            for max_len in args.contexts:
                row = run(spec, backend, max_len, threads=4)
                rows.append(row)
                print(f"{key:8s} {backend:6s} ctx={max_len:5d} load={row['load_seconds']:.2f}s "
                      f"totalPeakRSS={row['total_peak_rss_mb']:.1f}MB (parent {row['parent_rss_after_load_mb']:.1f} + scorer {row['scorer_rss_mb']:.1f}) "
                      f"avg={row['avg_latency_ms']:.1f}ms fail={row['failures']}")
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"written -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
