"""QA: 행 단위(row-level) 탐지 상세 - 어떤 행이 걸렸는지, 마스킹된 값만 보고서에 나온다."""
from __future__ import annotations

import csv
from pathlib import Path

from app.config.settings import AnalysisConfig
from app.core.analysis_service import analyse_file
from app.export.hit_exporter import collect_hit_rows, write_hit_rows


BUSINESS_TABLE = """CUST_NO,NAME,EMAIL,ADDR,DEPT,AMOUNT,PHONE
C1234567,홍길동,hong@example.com,서울시 강남구 테헤란로 151,영업,15000,010-1234-5678
D2345678,김철수,kim@example.com,부산시 해운대구 중동 20,engineering,25000,010-2345-6789
E3456789,박민a,park@example.com,경기도 성남시 성남동 3,marketing,30000,010-3456-7890
1004,이영희,lee@example.com,서울시 마포구-concord 4,finance,40000,010-4567-8901
9005,지원팀,support@example.com,서울시 supports 1,IT,1000,010-5555-6666
""".strip()


def _analyse(tmp_path: Path):
    path = tmp_path / "biz.csv"
    path.write_text(BUSINESS_TABLE, encoding="utf-8")
    return analyse_file(path, AnalysisConfig(use_ai=False))


def test_hit_rows_record_row_numbers_and_masked_values(tmp_path: Path):
    result = _analyse(tmp_path)
    finding = next(f for f in result.findings if f["column"] == "EMAIL")
    rows = finding["types"][0]["hit_rows"]
    assert rows, "EMAIL hits must carry per-row evidence"
    assert {row["row"] for row in rows} == {1, 2, 3, 4, 5}
    for row in rows:
        assert "*" in row["value"], f"value must be masked: {row['value']}"
    # raw text of the file must not appear verbatim in the detail list
    text = " ".join(row["value"] for row in rows)
    assert "hong@example.com" not in text
    assert "서울시 강남구 테헤란로 151" not in text


def test_identifier_columns_are_demoted_to_review_verdict(tmp_path: Path):
    """CUST_NO looks like a passport number but is an internal ID -> 검토, not 개인정보."""
    result = _analyse(tmp_path)
    finding = next(f for f in result.findings if f["column"] == "CUST_NO")
    decisions = {t["decision"] for t in finding["types"] + finding["weak_types"]}
    assert "부분 매칭(검토)" in decisions
    assert "개인정보" not in decisions

    email = next(f for f in result.findings if f["column"] == "EMAIL")
    assert email["types"][0]["decision"] == "개인정보"


def test_hit_csv_lists_every_flagged_row(tmp_path: Path):
    result = _analyse(tmp_path)
    out = write_hit_rows([result], tmp_path / "hits.csv")
    rows = list(csv.DictReader(open(out, encoding="utf-8-sig")))
    assert rows, "hit detail CSV must not be empty"
    assert {"컬럼명", "개인정보유형", "판정", "행번호", "탐지값(마스킹)"} <= set(rows[0])
    for row in rows:
        assert row["행번호"].isdigit()
        assert "*" in row["탐지값(마스킹)"] or len(row["탐지값(마스킹)"]) <= 3
        for secret in ("hong@example.com", "010-1234-5678", "테헤란로 151"):
            assert secret not in row["탐지값(마스킹)"]


def test_result_rows_expose_verdict_and_hit_text(tmp_path: Path, capsys):
    result = _analyse(tmp_path)
    email_row = next(r for r in result.results if r.column == "EMAIL")
    assert email_row.verdict == "개인정보"
    assert email_row.hit_rows and email_row.hit_rows[0]["row"] == 1
    assert "*" in email_row.hit_rows[0]["value"]
