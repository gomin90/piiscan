"""Accuracy / performance benchmark for the offline model evaluation.

    python tools/benchmark.py                      # rule engine + offline analyser
    python tools/benchmark.py --model models/x.gguf
    python tools/benchmark.py --rows 130000

Synthetic data only; no real personal information is used.
"""
from __future__ import annotations

import sys


import argparse
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai.llm_manager import LocalLLMEngine
from app.config.settings import AnalysisConfig
from app.core.analysis_service import analyse_file
from tests.fixtures.generate_fixtures import generate_large
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Ground truth by column name for the synthetic evaluation table.
GROUND_TRUTH = {
    "USER_NAME": {"NAME"},
    "PHONE": {"PHONE_NUMBER"},
    "RESIDENT_NO": {"RESIDENT_ID"},
    "EMAIL": {"EMAIL"},
    "ADDR": {"ADDRESS"},
    "BIRTH": {"BIRTH_DATE"},
    "LAT": {"LATITUDE", "LOCATION"},
    "LNG": {"LONGITUDE", "LOCATION"},
    "CARD_NO": {"CARD_NUMBER"},
    "BANK_ACCT": {"BANK_ACCOUNT"},
    "IMSI": {"IMSI"},
    "SEC_ID": {"CUSTOM_SEC_ID"},
    "ID_SUFFIX": {"CUSTOM_ID_SUFFIX"},
    "USER_ID": set(),
    "SN": {"SN"},
}


def evaluate(model: str | None, rows: int) -> dict:
    config = AnalysisConfig(batch_size=1000)
    path = generate_large(min(rows, 200000))
    engine = LocalLLMEngine(config)
    if model:
        config.use_ai = True
    started = time.perf_counter()
    result = analyse_file(path, config, engine)
    elapsed = time.perf_counter() - started

    detected = defaultdict(set)
    for row in result.results:
        detected[row.column].add(row.privacy_type)

    true_positive = false_positive = false_negative = 0
    for column, expected in GROUND_TRUTH.items():
        found = detected.get(column, set())
        if expected:
            if found & expected:
                true_positive += 1
            else:
                false_negative += 1
        else:
            false_positive += len(found)

    precision = true_positive / max(true_positive + false_positive, 1)
    recall = true_positive / max(true_positive + false_negative, 1)
    return {
        "rows": result.rows_parsed,
        "seconds": round(elapsed, 2),
        "rows_per_second": round(result.rows_parsed / max(elapsed, 0.001)),
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "ai_backend": engine.summary()["backend"],
        "ai_status": engine.summary()["status"],
        "false_positive_columns": {c: sorted(v) for c, v in detected.items() if not GROUND_TRUTH.get(c)},
        "missed": {c: sorted(expected) for c, expected in GROUND_TRUTH.items() if expected and not (detected.get(c, set()) & expected)},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Personal data detection benchmark")
    parser.add_argument("--model", default=None, help="GGUF model path (offline, no download)")
    parser.add_argument("--rows", type=int, default=130000)
    args = parser.parse_args()
    report = evaluate(args.model, args.rows)
    print("=== Privacy Scanner benchmark ===")
    for key, value in report.items():
        print(f"{key:>20}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
