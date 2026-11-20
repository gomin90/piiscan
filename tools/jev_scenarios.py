"""Jev-Style 시나리오(이름/주소/보호/컬럼 의미) 판정 Accuracy 실측.

합성 컬럼 30개로 C/D/E/F 시나리오 각각의 Accuracy와 confusion을 산출한다.
`python tools/jev_scenarios.py`
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.ai.gpu_detector import detect_hardware
from app.ai.jev.adapter import (
    ADDRESS_QUESTION,
    JevStyleAdapter,
    NAME_QUESTION,
    PROTECTION_QUESTION,
    build_column_state,
)
from app.ai.jev.decision import (
    ADDRESS_OPTIONS,
    CODE_MEANING_QUESTION,
    CODE_MEANING_OPTIONS,
    NAME_CONTEXT_OPTIONS,
    PROTECTION_OPTIONS,
    JevStyleDecisionRequest,
)
from app.ai.jev.model_manager import JevStyleModelManager

SURNAMES = ["김", "이", "박", "최", "정", "오", "윤", "홍"]
GIVEN = ["길동", "철수", "영희", "민준", "수지", "도준", "유나", "태준"]
NAMES = [f"{s}{g}" for s, g in zip(SURNAMES, GIVEN)]

CASES = [
    # C. 이름 문맥
    ("name", "USER_NM", NAMES, "REAL_PERSON_NAME"),
    ("name", "NM", NAMES[:4], "REAL_PERSON_NAME"),
    ("name", "CONTACT_PERSON", NAMES[:3], "REAL_PERSON_NAME"),
    ("name", "AUTHOR_NM", ["김진", "이항", "박미"], "REAL_PERSON_NAME"),
    ("name", "USER_ID", ["user_1024", "user_2048", "user_3001"], "USER_ID"),
    ("name", "LOGIN_ID", ["admin", "ops01", "svc_bot"], "USER_ID"),
    ("name", "COMPANY_NM", ["한국텔레콤", "아래 주식회사", "미래 시스템"], "COMPANY_NAME"),
    ("name", "VENDOR_NM", ["서울 전자 부품", " 경기 물류"], "COMPANY_NAME"),
    ("name", "DEPT_NM", ["마케팅팀", "지원팀", "개발팀", "재무팀"], "DEPARTMENT_NAME"),
    ("name", "ORG_UNIT", ["영업 1부", "품질관리팀"], "DEPARTMENT_NAME"),
    ("name", "PRODUCT_NM", ["노트프로 15", "무선 키보드", "결제 시스템"], "PRODUCT_NAME"),
    ("name", "REMARK", ["문제 없음", "테스트 메모", "nachtrag"], "GENERAL_TEXT"),
    ("name", "SYS_FLAG", ["Y", "N", "PENDING"], "GENERAL_TEXT"),
    # D. 주소
    ("address", "ADDR_ROAD", ["서울시 강남대로 121", "부산시 중앙로 45"], "ROAD_ADDRESS"),
    ("address", "ADDR_LOT", ["서울시 강남구 역삼동 12-3", "부산시 중앙동 45-1"], "LOT_ADDRESS"),
    ("address", "ADDR_DTL", ["501호", "3-402", "702호", "B-11"], "DETAIL_ADDRESS"),
    ("address", "CITY_NM", ["서울", "부산", "대구", "인천"], "REGION_NAME"),
    ("address", "PROVINCE", ["경기도", "서울특별시", "부산광역시"], "ADMIN_UNIT"),
    ("address", "ADDR_TYPE", ["road", "lot", "road"], "GENERAL_TEXT"),
    # E. 컬럼 의미
    ("meaning", "USER_NM", NAMES, "PERSONAL_DATA"),
    ("meaning", "MEMBER_INFO", ["25", "40", "31"], "PERSONAL_DATA"),
    ("meaning", "CUST_DATA", ["12", "30", "45"], "SYSTEM_OR_BUSINESS_CODE"),
    ("meaning", "ADDR_DTL", ["501호", "3-402"], "PERSONAL_DATA"),
    ("meaning", "PERS_NO", ["7901011234567", "8504122345678"], "PERSONAL_DATA"),
    ("meaning", "USER_NAME_MASK", ["김**", "이**"], "PERSONAL_DATA"),
    ("meaning", "ACCOUNT_HASH", ["9f86c083f1a4b2c7", "2c26b46b68ffc68f"], "PERSONAL_DATA"),
    ("meaning", "PHONE_ENC", ["U2VjcmV0ZWQgRGF0YQ==", "Zm9vYmFyYmF6ZXE="], "PERSONAL_DATA"),
    ("meaning", "ORDER_NO", ["OD20240101001", "OD20231205002"], "SYSTEM_OR_BUSINESS_CODE"),
    # F. 보호
    ("protection", "USER_NAME_MASK", ["김**", "이**", "박**"], "MASKED_COLUMN"),
    ("protection", "RESIDENT_HASH", ["9f86c083f1a4b2c7d6e5f4a3b2c1d0e9"], "HASHED_COLUMN"),
    ("protection", "PHONE_ENC", ["U2VjcmV0ZWQgRGF0YQ==", "Zm9vYmFyYmF6ZXE="], "ENCRYPTED_COLUMN"),
    ("protection", "EMAIL_MASK", ["a***@***.com", "b***@***.net"], "MASKED_COLUMN"),
    ("protection", "ID_UNKNOWN", ["a1b2c3d4e5", "9f8e7d6c5b"], "PROTECTION_UNKNOWN"),
    ("protection", "ADDR_ROAD", ["서울시 강남대로 121"], "SOURCE_PERSONAL_COLUMN"),
]

QUESTIONS = {
    "name": (NAME_QUESTION, NAME_CONTEXT_OPTIONS),
    "address": (ADDRESS_QUESTION, ADDRESS_OPTIONS),
    "protection": (PROTECTION_QUESTION, PROTECTION_OPTIONS),
    "meaning": (CODE_MEANING_QUESTION, CODE_MEANING_OPTIONS),
}


def evaluate(spec, hardware) -> dict:
    adapter = JevStyleAdapter(spec, hardware, threads=4)
    if not adapter.load():
        return {"model": spec.key, "loaded": False, "status": adapter.status}
    rows = []
    for scenario, column, samples, expected in CASES:
        question, options = QUESTIONS[scenario]
        state = build_column_state({
            "schema": "QA", "table": "SCENARIO", "column": column,
            "stats": {"rows": len(samples), "non_empty": len(samples), "null": 0,
                      "distinct_estimate": len(set(samples)), "samples": samples},
        })
        result = adapter.decide(JevStyleDecisionRequest(scenario, state, question, options, "choice", "general"))
        rows.append({
            "scenario": scenario, "column": column, "expected": expected,
            "predicted": result.decision, "confidence": round(result.confidence, 4),
            "margin": round(result.margin, 4), "error": result.error,
        })
    summary = adapter.summary()
    adapter.close()
    correct = [row for row in rows if row["predicted"] == row["expected"]]
    by_scenario = {}
    for scenario in QUESTIONS:
        subset = [row for row in rows if row["scenario"] == scenario]
        ok = sum(1 for row in subset if row["predicted"] == row["expected"])
        uncertain = sum(1 for row in subset if row["predicted"] == "UNCERTAIN")
        by_scenario[scenario] = {
            "n": len(subset), "correct": ok,
            "accuracy": round(ok / len(subset), 4),
            "uncertain": uncertain,
            "confusions": Counter(f"{row['expected']} -> {row['predicted']}"
                                  for row in subset if row["predicted"] != row["expected"]),
        }
    return {
        "model": spec.key, "loaded": True,
        "overall_accuracy": round(len(correct) / len(rows), 4),
        "by_scenario": by_scenario,
        "records": rows,
        "peak_rss_mb": summary["peak_rss_mb"],
        "load_seconds": summary["load_seconds"],
        "avg_latency_ms": round(summary["inference_seconds"] / max(summary["decisions"], 1) * 1000, 1),
    }


def main() -> int:
    hardware = detect_hardware()
    specs = {spec.key: spec for spec in JevStyleModelManager().discover()}
    out = {}
    for key in ("0.8b-v3", "2b-v4"):
        if key not in specs:
            continue
        result = evaluate(specs[key], hardware)
        out[key] = result
        print(f"== {key}: overall {result.get('overall_accuracy')}")
        for scenario, stats in result.get("by_scenario", {}).items():
            print(f"   {scenario}: {stats['correct']}/{stats['n']} acc={stats['accuracy']} "
                  f"uncertain={stats['uncertain']} confusions={dict(stats['confusions'])}")
    path = ROOT / "results" / "qa" / "jev_scenarios.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("written ->", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
