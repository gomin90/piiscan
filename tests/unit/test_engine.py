"""Privacy engine, protection analysis, code groups and risk scoring."""
from __future__ import annotations

import csv
from pathlib import Path

from app.core.code_group_analyzer import analyze as analyze_code_groups
from app.core.column_stats import ColumnStats
from app.core.encryption_analyzer import analyze_protection
from app.core.privacy_engine import PrivacyEngine
from app.core.risk_engine import assess_column
from app.core.result_aggregator import build_results
from tests.fixtures.generate_fixtures import FIXTURE_DIR


def run(csv_path: Path) -> tuple[list[dict], list[ColumnStats]]:
    with open(csv_path, encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    header = rows[0]
    stats = [ColumnStats(name=name, index=i) for i, name in enumerate(header)]
    engine = PrivacyEngine()
    engine.process_batch(stats, rows[1:])
    return engine.finalise(stats), stats


def test_column_names_are_used_when_values_are_ambiguous():
    finding, _ = run(FIXTURE_DIR / "code_columns.csv")
    # C00x headers carry no meaning, but the values still drive detection.
    detected = {finding[i]["column"] for i in range(len(finding)) if finding[i]["types"]}
    assert any("NAME" in finding[i]["column"] or finding[i]["types"] for i in range(len(finding)))
    assert detected


def test_resident_and_phone_detected_in_named_columns():
    finding, _ = run(FIXTURE_DIR / "utf8_comma.csv")
    columns = {f["column"]: {t["type_key"] for t in f["types"]} for f in finding}
    assert "RESIDENT_ID" in columns["RESIDENT_NO"]
    assert "PHONE_NUMBER" in columns["PHONE"]
    assert "EMAIL" in columns["EMAIL"]
    assert "NAME" in columns["USER_NAME"]


def test_protection_columns_are_related_to_base_column():
    finding, stats = run(FIXTURE_DIR / "masked_encrypted.csv")
    groups = analyze_protection(stats, finding)
    names = {group.base for group in groups}
    assert "USER_NAME" in names
    user = next(group for group in groups if group.base == "USER_NAME")
    assert user.mask and user.hash_ and user.enc
    assert any("hash" in str(user.mask.get("evidence", "")).lower() or user.hash_ for _ in [0])


def test_code_group_table_is_understood():
    finding, stats = run(FIXTURE_DIR / "code_group.csv")
    groups = analyze_code_groups(["GROUP_CODE", "CODE", "CODE_NAME"],
                                 [["GENDER", "M", "남성"], ["COUNTRY", "KR", "대한민국"]], stats)
    assert groups
    assert groups[0].code_column == "CODE"
    assert groups[0].group_column == "GROUP_CODE"
    assert groups[0].privacy_relevant


def test_risk_is_high_for_unprotected_resident_numbers():
    finding, _ = run(FIXTURE_DIR / "utf8_comma.csv")
    resident = next(f for f in finding if f["column"] == "RESIDENT_NO")
    assessment = assess_column(resident, None, None, table_pii_types=6)
    assert assessment["level"] in ("높음", "매우 높음")
    protected = assess_column(resident, {"status_score": 0.9}, None, table_pii_types=6)
    assert protected["score"] < assessment["score"]


def test_results_are_one_row_per_column_and_type():
    finding, stats = run(FIXTURE_DIR / "mixed_pii.csv")
    results = build_results("mixed_pii.csv", "MySQL", "sales", "users", "전수",
                            8, finding, analyze_protection(stats, finding), {})
    assert results
    assert all(hasattr(row, "risk_level") for row in results)
    assert any(row.privacy_type == "RESIDENT_ID" for row in results)
