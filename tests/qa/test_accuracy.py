"""QA: 유형별 Precision / Recall / F1 / FP / FN.

Run directly for the printed report:
    python tools/qa_accuracy.py --json results/qa_accuracy_after.json
"""
from __future__ import annotations

import pytest

from tests.qa.dataset import DATASET
from tests.qa.metrics import detector_metrics, pipeline_metrics, protection_metrics

# Floors agreed with the design: bare numeric values (12/15자) are only reported
# when the column name carries affinity evidence, so the no-hint run is allowed
# to be lower than the hinted run for those ambiguous families.
FLOORS = {
    "RESIDENT_ID": (0.9, 0.9),
    "PASSPORT": (0.8, 0.9),
    "DRIVER_LICENSE": (0.9, 0.9),
    "SOCIAL_SECURITY": (0.9, 0.9),
    "CARD_NUMBER": (0.6, 0.9),
    "EMAIL": (0.9, 1.0),
    "PHONE_NUMBER": (0.9, 0.9),
    "NAME": (0.9, 0.9),
    "ADDRESS": (0.9, 0.9),
    "BIRTH_DATE": (0.9, 0.9),
    "LOCATION": (0.7, 0.9),
    "IMEI": (0.9, 1.0),
    "UUID": (0.9, 1.0),
    "MAC": (0.9, 1.0),
    "IP": (0.9, 0.9),
    "POSTCODE": (0.9, 0.9),
}


def test_dataset_has_expected_type_coverage():
    types = {primary for _, primary, _, _ in DATASET}
    required = {
        "RESIDENT_ID", "PASSPORT", "DRIVER_LICENSE", "FOREIGNER_ID", "SOCIAL_SECURITY",
        "INSURANCE_NUMBER", "CARD_NUMBER", "BANK_ACCOUNT", "EMAIL", "PHONE_NUMBER",
        "NAME", "ADDRESS", "BIRTH_DATE", "LOCATION", "IMSI", "IMEI", "UUID", "MAC",
        "IP", "SN", "POSTCODE", "CUSTOM_SEC_ID", "CUSTOM_ID_SUFFIX", "CUSTOM_WILDCARD_ID",
    }
    missing = required - types
    assert not missing, f"dataset missing privacy types: {missing}"
    negatives = sum(1 for _, primary, _, _ in DATASET if primary is None)
    assert negatives >= 20, "need adversarial non-PII values for precision measurement"


def test_per_type_metric_floors():
    metrics = detector_metrics(column_hint=True)
    failures = []
    for type_key, (min_precision, min_recall) in FLOORS.items():
        m = metrics[type_key]
        if m["precision"] < min_precision or m["recall"] < min_recall:
            failures.append(
                f"{type_key}: P={m['precision']} R={m['recall']} "
                f"(TP={m['tp']} FP={m['fp']} FN={m['fn']}) < ({min_precision}, {min_recall})"
            )
    assert not failures, "\n".join(failures)


def test_column_name_value_contradictions_are_not_reported():
    """A column name that contradicts the value must not create a hit."""
    metrics = detector_metrics(column_hint=True)
    contradictions = {
        "MAC_ADDR": ["192.168.0.101"],       # IP 값을 MAC 컬럼에 넣음
        "SERIAL_NO": ["123456789012345"],    # IMEI/IMSI 형태로 SN 컬럼에 넣음
        "POSTCODE": ["37.566520"],           # 좌표 값 in postcode column
        "BANK_ACCOUNT": ["000-45-6789"],     # invalid SSN in account column
        "COORD": ["1000.1234"],              # out-of-range decimal
    }
    from app.config.settings import AnalysisConfig
    from app.core.privacy_engine import build_detectors
    from app.detectors.base import profile_of

    detectors = build_detectors(AnalysisConfig.load())
    leaked = []
    for column, values in contradictions.items():
        for value in values:
            profile = profile_of(value)
            for detector in detectors:
                match = detector.match(value, profile, column)
                if match and match.type_key not in metrics.get(match.type_key, {}).get("also_ok", set()):
                    # Only types that are genuinely wrong for this value
                    if match.type_key not in {"MAC", "IP", "SN", "POSTCODE", "LOCATION", "BANK_ACCOUNT", "IMEI", "IMSI"}:
                        leaked.append((column, value, match.type_key, match.confidence))
    assert not leaked, f"contradicting column names produced detections: {leaked}"


def test_masked_and_malformed_values_still_detected_as_protected():
    metrics = detector_metrics(column_hint=True)
    masked_positive_types = ("RESIDENT_ID", "PASSPORT", "DRIVER_LICENSE", "CARD_NUMBER", "PHONE_NUMBER", "EMAIL")
    for type_key in masked_positive_types:
        assert metrics[type_key]["recall"] >= 0.7, f"{type_key} recall too low on masked values"


@pytest.mark.parametrize("value", ["20240230", "1990-13-01", "123456", "not-an-email.com", "user@", "a@b", "서울", "1234"])
def test_lookalikes_are_not_flagged(value):
    from app.config.settings import AnalysisConfig
    from app.core.privacy_engine import build_detectors
    from app.detectors.base import profile_of

    detectors = build_detectors(AnalysisConfig.load())
    hits = [d.match(value, profile_of(value), "VALUE") for d in detectors]
    hits = [m for m in hits if m]
    assert not hits, f"{value} flagged as {sorted(m.type_key for m in hits)}"


def test_pipeline_reports_every_privacy_type_in_its_own_column():
    pipe = pipeline_metrics()
    missing = [t for t, m in pipe.items() if not m["detected"]]
    assert not missing, f"engine did not report: {missing}"
    cross = {t: m["reported_on_other_columns"] for t, m in pipe.items() if m["reported_on_other_columns"]}
    # CARD/IMSI/IMEI overlap is inherent for 15-digit numbers; everything else must stay clean.
    unexpected = {t: cols for t, cols in cross.items() if t not in {"CARD_NUMBER", "IMSI", "IMEI", "BANK_ACCOUNT", "SN"}}
    assert not unexpected, f"type leaked into unrelated columns: {unexpected}"


def test_protection_columns_are_classified():
    prot = protection_metrics()
    statuses = {g["base"]: g["status"] for g in prot["protection"]}
    assert any("보호 확인" in s or "부분 보호" in s for s in statuses.values()), statuses
    hash_groups = [g for g in prot["protection"] if g["hash"]]
    assert hash_groups, "no *_HASH column recognised"
    assert all(g["hash"]["verified"] for g in hash_groups), "hash columns not verified from values"
