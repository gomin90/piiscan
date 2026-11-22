"""AI 입력 State 설계 ablation: 어떤 state 필드 조합이 판정 정확도를 높이는지실측."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.ai.jev.adapter import (PERSONALITY_QUESTION, TYPE_QUESTION, build_column_state,
                                charset_profile, pattern_counts, shape_pattern, structure_hints)
from app.ai.jev.decision import JEV_TYPE_TO_INTERNAL, JevStyleDecisionRequest, PERSONALITY_OPTIONS, TYPE_OPTIONS
from app.ai.jev.model_manager import JevStyleModelManager
from app.ai.gpu_detector import detect_hardware
from app.ai.jev.adapter import JevStyleAdapter
from tests.qa.jev_dataset import examples


def make_state(case, variant):
    column = {"schema": "TB", "table": "T", "column": case["column"],
              "stats": {"rows": len(case["samples"]), "non_empty": len(case["samples"]), "null": 0,
                        "distinct_estimate": len(set(case["samples"])), "samples": case["samples"]}}
    state = build_column_state(column)
    if variant == "no_charset":
        state.pop("charset", None)
    elif variant == "patterns_only":
        state = {"column": case["column"], "column_type": "TEXT",
                 "value_patterns": state["value_patterns"], "charset": state["charset"]}
    elif variant == "no_samples":
        state.pop("masked_samples")
    elif variant == "no_patterns":
        state.pop("value_patterns", None)
    return state


def main():
    hardware = detect_hardware()
    cases = examples()
    variants = ["base", "no_charset", "no_patterns", "no_samples"]
    results = {}
    for key in ("0.8b-v3", "2b-v4"):
        spec = JevStyleModelManager().select(64, key)
        adapter = JevStyleAdapter(spec, hardware, threads=4)
        adapter.load()
        for variant in variants:
            requests = []
            for case in cases:
                state = make_state(case, variant)
                requests.append(JevStyleDecisionRequest("type", state, TYPE_QUESTION, TYPE_OPTIONS, "choice", "intent"))
            outs = adapter.decide_many(requests)
            type_acc = sum(1 for case, out in zip(cases, outs)
                           if JEV_TYPE_TO_INTERNAL.get(out.decision, "") == case["expected_type"])
            requests = [JevStyleDecisionRequest("p", make_state(c, variant), PERSONALITY_QUESTION,
                                               PERSONALITY_OPTIONS, "choice", "general") for c in cases]
            outs = adapter.decide_many(requests)
            person_acc = sum(1 for case, out in zip(cases, outs) if out.decision == case["expected_personality"])
            tokens = sum(out.input_tokens for out in outs) / len(outs)
            print(f"{key:8s} {variant:14s} type={type_acc}/{len(cases)} ({type_acc/len(cases):.3f}) "
                  f"personality={person_acc}/{len(cases)} ({person_acc/len(cases):.3f}) avg_tokens={tokens:.0f}")
            results[(key, variant)] = {"type": type_acc / len(cases), "personality": person_acc / len(cases),
                                       "avg_tokens": round(tokens, 1)}
        adapter.close()
    Path("results/qa/jev_state_ablation.json").write_text(
        json.dumps({f"{k[0]}|{k[1]}": v for k, v in results.items()}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
