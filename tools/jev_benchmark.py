"""Jev-Style 모델별 한국어 개인정보 판정 평가.

`python tools/jev_benchmark.py --models 0.8b-v3 2b-v4`
 합성 데이터셋(tests/qa/jev_dataset.py)으로 Accuracy / Precision / Recall / F1 /
 FP / FN / UNCERTAIN 비율, 판정 시간, 모델 로딩 시간, peak RSS를 산출한다.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.ai.gpu_detector import detect_hardware
from app.ai.jev.adapter import JevStyleAdapter, build_column_state
from app.ai.jev.decision import (
    ADDRESS_OPTIONS,
    JEV_TYPE_TO_INTERNAL,
    NAME_CONTEXT_OPTIONS,
    PERSONALITY_OPTIONS,
    PROTECTION_OPTIONS,
    TYPE_OPTIONS,
    JevStyleDecisionRequest,
)
from app.ai.jev.model_manager import JevStyleModelManager
from tests.qa.jev_dataset import examples

SCENARIO_OPTIONS = {
    "personality": PERSONALITY_OPTIONS,
    "type": TYPE_OPTIONS,
    "name_context": NAME_CONTEXT_OPTIONS,
    "address": ADDRESS_OPTIONS,
    "protection": PROTECTION_OPTIONS,
}

CONTEXT_TO_INTERNAL = {
    "REAL_PERSON_NAME": "NAME",
    "USER_ID": "CUSTOM_IDENTIFIER",
    "COMPANY_NAME": "NONE",
    "DEPARTMENT_NAME": "NONE",
    "PRODUCT_NAME": "NONE",
    "GENERAL_TEXT": "NONE",
    "ROAD_ADDRESS": "ADDRESS",
    "LOT_ADDRESS": "ADDRESS",
    "DETAIL_ADDRESS": "ADDRESS",
    "REGION_NAME": "NONE",
    "ADMIN_UNIT": "NONE",
    "SOURCE_PERSONAL_COLUMN": "SOURCE",
    "MASKED_COLUMN": "MASK",
    "HASHED_COLUMN": "HASH",
    "ENCRYPTED_COLUMN": "ENC",
    "PROTECTION_UNKNOWN": "UNKNOWN",
}

PERSONAL_LABELS = {"PERSONAL_DATA", "SENSITIVE_PERSONAL_DATA"}


def run_model(spec, hardware, cases) -> dict:
    adapter = JevStyleAdapter(spec, hardware, threads=4)
    loaded = adapter.load()
    records = []
    for case in cases:
        state = build_column_state({
            "schema": "QA", "table": "JEV_EVAL", "column": case["column"],
            "stats": {"rows": len(case["samples"]), "non_empty": len(case["samples"]),
                      "null": 0, "distinct_estimate": len(set(case["samples"])),
                      "samples": case["samples"]},
        })
        requests = [
            JevStyleDecisionRequest("personality", state, *personality_question()),
            JevStyleDecisionRequest("type", state, *type_question()),
        ]
        column = case["column"].upper()
        if case["scenario"] == "name_context":
            requests.append(JevStyleDecisionRequest("name_context", state, *name_question()))
        if case["scenario"] == "address":
            requests.append(JevStyleDecisionRequest("address", state, *address_question()))
        if case["scenario"] == "protection":
            requests.append(JevStyleDecisionRequest("protection", state, *protection_question()))
        results = adapter.decide_many(requests)
        by_scenario = {result.scenario: result for result in results}
        records.append({
            "column": case["column"],
            "samples": state["masked_samples"],
            "expected_personality": case["expected_personality"],
            "expected_type": case["expected_type"],
            "personality": by_scenario["personality"].decision,
            "personality_prob": round(by_scenario["personality"].confidence, 4),
            "jev_type": by_scenario["type"].decision,
            "internal_type": JEV_TYPE_TO_INTERNAL.get(by_scenario["type"].decision or "", "UNCERTAIN"),
            "type_prob": round(by_scenario["type"].confidence, 4),
            "type_margin": round(by_scenario["type"].margin, 4),
            "context": next((by_scenario[name].decision for name in ("name_context", "address", "protection")
                             if name in by_scenario), None),
            "context_prob": next((round(by_scenario[name].confidence, 4) for name in ("name_context", "address", "protection")
                                  if name in by_scenario), None),
            "tokens": by_scenario["type"].input_tokens,
            "errors": [result.error for result in results if result.error],
        })
    summary = adapter.summary()
    adapter.close()
    return {"model": spec.key, "loaded": loaded, "summary": summary, "records": records}


def personality_question():
    from app.ai.jev.adapter import PERSONALITY_QUESTION
    return PERSONALITY_QUESTION, PERSONALITY_OPTIONS, "choice", "general"


def type_question():
    from app.ai.jev.adapter import TYPE_QUESTION
    return TYPE_QUESTION, TYPE_OPTIONS, "choice", "intent"


def name_question():
    from app.ai.jev.adapter import NAME_QUESTION
    return NAME_QUESTION, NAME_CONTEXT_OPTIONS, "choice", "general"


def address_question():
    from app.ai.jev.adapter import ADDRESS_QUESTION
    return ADDRESS_QUESTION, ADDRESS_OPTIONS, "choice", "general"


def protection_question():
    from app.ai.jev.adapter import PROTECTION_QUESTION
    return PROTECTION_QUESTION, PROTECTION_OPTIONS, "choice", "general"


def metrics(records: list[dict]) -> dict:
    type_rows = [r for r in records]
    correct = sum(1 for r in type_rows if r["internal_type"] == r["expected_type"])
    uncertain = sum(1 for r in type_rows if r["internal_type"] == "UNCERTAIN" or r["jev_type"] == "UNCERTAIN")

    per_type: dict[str, dict] = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    for record in type_rows:
        predicted, expected = record["internal_type"], record["expected_type"]
        if predicted == expected:
            per_type[expected]["tp"] += 1
        else:
            if predicted != "UNCERTAIN":
                per_type[predicted]["fp"] += 1
            if expected != "UNCERTAIN":
                per_type[expected]["fn"] += 1

    type_metrics = {}
    for name, counts in sorted(per_type.items()):
        tp, fp, fn = counts["tp"], counts["fp"], counts["fn"]
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        type_metrics[name] = {"tp": tp, "fp": fp, "fn": fn,
                              "precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3)}

    person_tp = sum(1 for r in records if r["personality"] in PERSONAL_LABELS and r["expected_personality"] in PERSONAL_LABELS)
    person_fp = sum(1 for r in records if r["personality"] in PERSONAL_LABELS and r["expected_personality"] not in PERSONAL_LABELS)
    person_fn = sum(1 for r in records if r["personality"] not in PERSONAL_LABELS and r["expected_personality"] in PERSONAL_LABELS)
    person_tn = sum(1 for r in records if r["personality"] not in PERSONAL_LABELS and r["expected_personality"] not in PERSONAL_LABELS)
    person_uncertain = sum(1 for r in records if r["personality"] in ("UNCERTAIN", None))
    person_accuracy = (person_tp + person_tn) / len(records)
    person_precision = person_tp / (person_tp + person_fp) if person_tp + person_fp else 0.0
    person_recall = person_tp / (person_tp + person_fn) if person_tp + person_fn else 0.0
    person_f1 = 2 * person_precision * person_recall / (person_precision + person_recall) if person_precision + person_recall else 0.0

    return {
        "n": len(records),
        "type_accuracy": round(correct / len(records), 4),
        "type_uncertain": uncertain,
        "per_type": type_metrics,
        "personality": {
            "accuracy": round(person_accuracy, 4), "precision": round(person_precision, 4),
            "recall": round(person_recall, 4), "f1": round(person_f1, 4),
            "tp": person_tp, "fp": person_fp, "fn": person_fn, "tn": person_tn,
            "uncertain": person_uncertain,
        },
    }




def rule_baseline(cases) -> dict:
    """Run the existing rule engine on the same synthetic columns (AI disabled)."""
    import csv
    from pathlib import Path
    from app.core.analysis_service import analyse_file
    from app.config.settings import AnalysisConfig

    columns = []
    seen = {}
    for index, case in enumerate(cases):
        name = case["column"]
        if name in seen:
            name = f"{name}_{index}"
        seen[name] = True
        columns.append((name, case["samples"]))
    path = Path("results/qa/jev_rule_baseline.csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([name for name, _ in columns])
        width = max(len(samples) for _, samples in columns)
        for row in range(width):
            writer.writerow([samples[row] if row < len(samples) else "" for _, samples in columns])
    result = analyse_file(path, AnalysisConfig(use_ai=False))
    found = {}
    for row in result.results:
        found.setdefault(row.column, set()).add(row.privacy_type)
    return {"file": str(path), "per_column": {k: sorted(v) for k, v in found.items()}}


def rule_metric(cases, baseline: dict) -> dict:
    name_to_case = {}
    for index, case in enumerate(cases):
        key = case["column"] if case["column"] not in name_to_case else f"{case['column']}_{index}"
        name_to_case[key] = case
    correct = 0
    per_type = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    for key, case in name_to_case.items():
        types = set(baseline["per_column"].get(key, set()))
        expected = case["expected_type"]
        if expected in types:
            correct += 1
            per_type[expected]["tp"] += 1
        else:
            if expected != "UNCERTAIN":
                per_type[expected]["fn"] += 1
            for t in types:
                if t != expected:
                    per_type[t]["fp"] += 1
    detail = {}
    for name, counts in sorted(per_type.items()):
        tp, fp, fn = counts["tp"], counts["fp"], counts["fn"]
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        detail[name] = {"tp": tp, "fp": fp, "fn": fn, "precision": round(precision, 3),
                        "recall": round(recall, 3),
                        "f1": round(2 * precision * recall / (precision + recall), 3) if precision + recall else 0.0}
    return {"accuracy": round(correct / len(name_to_case), 4), "n": len(name_to_case), "per_type": detail}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Jev-Style decision model benchmark")
    parser.add_argument("--models", nargs="*", default=["0.8b-v3", "2b-v4"])
    parser.add_argument("--output", default="results/qa/jev_benchmark.json")
    args = parser.parse_args(argv)

    hardware = detect_hardware()
    manager = JevStyleModelManager()
    available = {spec.key: spec for spec in manager.discover()}
    cases = examples()
    baseline = rule_baseline(cases)
    results = {"rule_baseline": {"accuracy": rule_metric(cases, baseline)["accuracy"],
                                 "detail": rule_metric(cases, baseline),
                                 "csv": baseline["file"]}}
    for key in args.models:
        spec = available.get(key)
        if spec is None:
            print(f"[skip] {key}: 모델/scorer 없음 (download + build required)")
            continue
        started = time.perf_counter()
        payload = run_model(spec, hardware, cases)
        payload["elapsed_seconds"] = round(time.perf_counter() - started, 2)
        payload["metrics"] = metrics(payload["records"])
        results[key] = payload
        print(f"[done] {key}: type_acc={payload['metrics']['type_accuracy']:.3f} "
              f"load={payload['summary']['load_seconds']}s rss={payload['summary']['peak_rss_mb']}MB "
              f"decisions={payload['summary']['decisions']} failures={payload['summary']['failures']}")

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"written -> {out}")
    return 0 if results else 1


if __name__ == "__main__":
    raise SystemExit(main())
