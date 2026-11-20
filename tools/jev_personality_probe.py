"""개인정보 여부(personality) 판정 question/option 형태 비교.

Jev-Style이支持하는 question type(choice vs noul(true/false)) 중
어떤 형태가 한국어 PII 판정에서 더 정확한지实测한다.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.ai.jev.adapter import JevStyleAdapter, build_column_state
from app.ai.jev.decision import JevStyleDecisionRequest
from app.ai.jev.model_manager import JevStyleModelManager
from app.ai.gpu_detector import detect_hardware
from tests.qa.jev_dataset import examples

PERSONAL = {"PERSONAL_DATA", "SENSITIVE_PERSONAL_DATA"}

VARIANTS = {
    "choice4": (
        "이 컬럼은 개인정보를 포함하거나 개인정보를 표현하는 컬럼인가?",
        {"PERSONAL_DATA": "특정 개인을 식별하거나 개인과 연결할 수 있는 데이터 (이름, 전화번호, 이메일, 집 주소)",
         "SENSITIVE_PERSONAL_DATA": "주민등록번호, 카드번호, 계좌, 생체 정보 등 민감 개인 정보",
         "NON_PERSONAL_DATA": "업무 코드, 회사/부서 명, 시스템 값, 일반 텍스트 등 개인과 무관한 데이터",
         "UNCERTAIN": "문맥 부족으로 판단할 수 없음"}, "choice"),
    "noul": (
        "이 컬럼은 개인 정보 컬럼이다",
        {"false": "개인 정보와 무관한 업무 코드/시스템 값/일반 texto", "true": "개인을 식별하거나 개인과 연결된 데이터"},
        "noul"),
    "noul_bare": ("이 컬럼은 개인 정보 컬럼이다", None, "noul"),
    "choice3": ("이 컬럼은 개인 정보와 관련 있는 컬럼인가?",
                {"PERSONAL_DATA": "개인 정보 포함", "NON_PERSONAL_DATA": "개인 정보 무관",
                 "UNCERTAIN": "불명"}, "choice"),
}


def main():
    hardware = detect_hardware()
    cases = examples()
    rows = []
    for key in ("0.8b-v3", "2b-v4"):
        adapter = JevStyleAdapter(JevStyleModelManager().select(64, key), hardware, threads=4)
        adapter.load()
        for name, (question, options, qtype) in VARIANTS.items():
            requests = []
            for case in cases:
                state = build_column_state({"schema": "TB", "table": "T", "column": case["column"],
                    "stats": {"rows": len(case["samples"]), "non_empty": len(case["samples"]), "null": 0,
                              "distinct_estimate": len(set(case["samples"])), "samples": case["samples"]}})
                requests.append(JevStyleDecisionRequest(name, state, question, options, qtype, "general"))
            outs = adapter.decide_many(requests)
            tp = fp = fn = tn = uncertain = 0
            exact = 0
            for case, out in zip(cases, outs):
                expected = case["expected_personality"]
                if expected == "UNCERTAIN":
                    uncertain += 1
                    continue
                predicted_personal = out.decision in PERSONAL or out.decision == "true"
                expected_personal = expected in PERSONAL
                if predicted_personal and expected_personal:
                    tp += 1
                elif predicted_personal and not expected_personal:
                    fp += 1
                elif not predicted_personal and expected_personal:
                    fn += 1
                else:
                    tn += 1
                if out.decision == expected:
                    exact += 1
            n = tp + fp + fn + tn
            acc = (tp + tn) / n if n else 0.0
            precision = tp / (tp + fp) if tp + fp else 0.0
            recall = tp / (tp + fn) if tp + fn else 0.0
            f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
            print(f"{key:8s} {name:11s} acc={acc:.3f} P={precision:.3f} R={recall:.3f} F1={f1:.3f} "
                  f"fp={fp} fn={fn} exact={exact}/{len(cases)}")
            rows.append({"model": key, "variant": name, "accuracy": round(acc, 3), "precision": round(precision, 3),
                         "recall": round(recall, 3), "f1": round(f1, 3), "fp": fp, "fn": fn, "uncertain": uncertain})
        adapter.close()
    Path("results/qa/jev_personality_probe.json").write_text(
        __import__("json").dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
