#!/usr/bin/env python3
"""Jev-Style decision-path smoke test (synthetic columns only, no personal data).

Runs a fixed set of synthetic columns through the adapter and writes a
machine-readable report. Intended for Windows validation, where the scorer may
have just been rebuilt on the target machine.

    python tools/jev_smoke.py
    python tools/jev_smoke.py --model 2b-v4 --cpu-only --output results/smoke_2b-v4.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.ai.gpu_detector import detect_hardware
from app.ai.jev.adapter import JevStyleAdapter
from app.ai.jev.model_manager import JevStyleModelManager
from app.config.settings import project_root

CASES = [
    {"column": "USER_NM", "column_type": "VARCHAR", "gold": "NAME",
     "samples": ["김**", "이**", "박**", "최**"], "distinct": 20, "non_empty": 20},
    {"column": "DEPT_CD", "column_type": "VARCHAR", "gold": "NON_PERSONAL_DATA",
     "samples": ["SALES", "FIN", "IT", "OPS"], "distinct": 4, "non_empty": 20},
    {"column": "ADDR_DTL", "column_type": "VARCHAR", "gold": "ADDRESS",
     "samples": ["서울**구 **로 12", "부산**구 **길 34"], "distinct": 20, "non_empty": 20},
    {"column": "PHONE_NO", "column_type": "VARCHAR", "gold": "PHONE_NUMBER",
     "samples": ["01*********", "02*********"], "distinct": 20, "non_empty": 20},
    {"column": "ACCOUNT_HASH", "column_type": "CHAR", "gold": "NON_PERSONAL_DATA",
     "samples": ["a1b2c3d4e5f6g7h8", "9011121314151617"], "distinct": 20, "non_empty": 20},
    {"column": "BIRTH_DT", "column_type": "DATE", "gold": "DATE_OF_BIRTH",
     "samples": ["198*0*1*", "199*1*2*"], "distinct": 18, "non_empty": 20},
]


def make_column(case: dict) -> dict:
    samples = case["samples"]
    lengths = [len(sample) for sample in samples]
    return {
        "column": case["column"],
        "column_type": case["column_type"],
        "schema": "TB_MEMBER",
        "table": "TB_MEMBER",
        "stats": {
            "rows": 20,
            "samples": samples,
            "null": 0,
            "non_empty": case["non_empty"],
            "distinct_estimate": case["distinct"],
            "numeric_ratio": sum(1 for sample in samples if sample.isdigit()) / len(samples),
            "masked_like": sum(1 for sample in samples if "*" in sample),
            "min_len": min(lengths),
            "max_len": max(lengths),
            "avg_len": sum(lengths) / len(lengths),
        },
    }


def run(model_key: str, cpu_only: bool, output: Path) -> int:
    manager = JevStyleModelManager()
    specs = {spec.key: spec for spec in manager.discover()}
    if model_key not in specs:
        print(f"[fail] model '{model_key}' not found. available: {', '.join(sorted(specs)) or 'none'}")
        for spec in specs.values():
            for problem in spec.problems:
                print(f"  {spec.key}: {problem}")
        return 1

    spec = specs[model_key]
    for problem in spec.problems:
        print(f"[warn] {spec.key}: {problem}")
    if not spec.verified:
        print(f"[fail] {model_key} integrity check failed -> rule-only mode")
        return 1

    hardware = detect_hardware()
    if cpu_only:
        hardware.backend = "cpu"

    started = time.perf_counter()
    adapter = JevStyleAdapter(spec, hardware=hardware, threads=2)
    loaded = adapter.load()
    load_seconds = time.perf_counter() - started
    if not loaded:
        print(f"[fail] model load failed: {adapter.last_error}")
        return 1

    correct = 0
    details = []
    for case in CASES:
        ai = adapter.classify_column(make_column(case))
        ok = ai["jev_type"] == case["gold"]
        correct += int(ok)
        details.append({
            "column": case["column"],
            "gold": case["gold"],
            "jev_type": ai["jev_type"],
            "confidence": ai["confidence"],
            "requires_review": ai["requires_review"],
            "reason": ai["reason"],
        })
        print(f"  {case['column']:14s} gold={case['gold']:18s} jev={str(ai['jev_type']):18s} "
              f"p={ai['confidence']:.2f} {'OK' if ok else 'MISMATCH'}")

    adapter.close()
    report = {
        "model": spec.model_name,
        "model_key": spec.key,
        "quant": spec.quant,
        "verified": spec.verified,
        "scorer": str(spec.scorer_path),
        "backend": adapter.backend,
        "load_seconds": round(load_seconds, 3),
        "cases": len(CASES),
        "correct": correct,
        "accuracy": round(correct / len(CASES), 4),
        "avg_decision_ms": round(adapter.inference_seconds / max(adapter.decisions, 1) * 1000, 1),
        "peak_rss_mb": round(adapter.peak_rss_mb, 1),
        "decisions": adapter.decisions,
        "failures": adapter.failures,
        "details": details,
        "privacy_note": "synthetic columns only; no raw personal data used or reported",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[ok] {model_key}: {correct}/{len(CASES)} matched, load {load_seconds:.2f}s, "
          f"avg {report['avg_decision_ms']}ms, peak RSS {report['peak_rss_mb']}MB")
    print(f"[ok] report: {output}")
    # The smoke test proves the decision path works; accuracy is measured by
    # tools/jev_benchmark.py. Non-zero only when decisions could not be produced.
    return 0 if adapter.decisions > 0 and adapter.failures == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Jev-Style decision smoke test")
    parser.add_argument("--model", default="0.8b-v3", help="0.8b-v3 or 2b-v4")
    parser.add_argument("--cpu-only", action="store_true")
    parser.add_argument("--output", default="results/smoke.json")
    args = parser.parse_args()
    return run(args.model, args.cpu_only, ROOT / args.output)


if __name__ == "__main__":
    raise SystemExit(main())
