"""Type question option wording probe: 단순 label vs 패턴 상세 description."""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.ai.jev.adapter import JevStyleAdapter, TYPE_QUESTION, build_column_state
from app.ai.jev.decision import JEV_TYPE_TO_INTERNAL, JevStyleDecisionRequest
from app.ai.jev.model_manager import JevStyleModelManager
from app.ai.gpu_detector import detect_hardware
from tests.qa.jev_dataset import examples

SIMPLE = {
    "RESIDENT_REGISTRATION_NUMBER": "주민등록번호",
    "PASSPORT_NUMBER": "여권번호",
    "DRIVER_LICENSE": "운전면허번호",
    "FOREIGNER_REGISTRATION_NUMBER": "외국인등록번호",
    "NAME": "이름",
    "DATE_OF_BIRTH": "생년월일",
    "PHONE_NUMBER": "전화번호",
    "EMAIL": "이메일",
    "ADDRESS": "주소",
    "BANK_ACCOUNT": "은행 계좌",
    "CARD_NUMBER": "카드 번호",
    "SOCIAL_SECURITY_NUMBER": "사회보장번호",
    "INSURANCE_NUMBER": "보험 번호",
    "BIOMETRIC_DATA": "생체 정보",
    "IMSI": "IMSI",
    "IMEI": "IMEI",
    "GEOLOCATION": "좌표",
    "CUSTOM_ID": "_CUSTOM identifier".replace("_CUSTOM", "CUSTOM"),
    "OTHER_PERSONAL_DATA": "기타 개인 정보",
    "NON_PERSONAL_DATA": "개인 정보 아님",
    "UNCERTAIN": "불명",
}


def main():
    hardware = detect_hardware()
    cases = examples()
    for key in ("0.8b-v3", "2b-v4"):
        adapter = JevStyleAdapter(JevStyleModelManager().select(64, key), hardware, threads=4)
        adapter.load()
        from app.ai.jev.decision import TYPE_OPTIONS
        for label, options in (("simple", SIMPLE), ("detailed", TYPE_OPTIONS), ("names_only", {k: None for k in SIMPLE})):
            requests = [JevStyleDecisionRequest("t", build_column_state({
                "schema": "TB", "table": "T", "column": c["column"],
                "stats": {"rows": len(c["samples"]), "non_empty": len(c["samples"]), "null": 0,
                          "distinct_estimate": len(set(c["samples"])), "samples": c["samples"]}}),
                TYPE_QUESTION, options, "choice", "intent") for c in cases]
            outs = adapter.decide_many(requests)
            acc = sum(1 for c, o in zip(cases, outs) if JEV_TYPE_TO_INTERNAL.get(o.decision, "") == c["expected_type"])
            unc = sum(1 for o in outs if o.decision == "UNCERTAIN")
            print(f"{key:8s} {label:12s} type_acc={acc}/{len(cases)} ({acc/len(cases):.3f}) uncertain={unc}")
        adapter.close()


if __name__ == "__main__":
    main()
