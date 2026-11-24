"""Cell-level rule tests for context-sensitive verdicts.

These cover the cases where a value matches a privacy *shape* but the surrounding
column or sentence says otherwise. Each assertion would fail without the rule it
documents, so they are the evidence for the precision/recall numbers reported by
tools/gold_accuracy.py.
"""
from __future__ import annotations

from pathlib import Path

from app.config.settings import AnalysisConfig
from app.core.analysis_service import analyse_file
from app.core.cell_verdict import STATUS_PII, STATUS_REVIEW
from app.detectors.base import profile_of
from app.detectors.address import AddressDetector
from app.detectors.birth_date import BirthDateDetector
from app.detectors.free_text_name import FreeTextNameDetector
from app.detectors.imei import ImeiDetector
from app.detectors.credit_card import CreditCardDetector


def _verdicts(rows: list[list[str]], header: list[str]) -> dict[tuple[int, str], dict]:
    import csv
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "case.csv"
        with open(path, "w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            writer.writerows(rows)
        result = analyse_file(path, AnalysisConfig(use_ai=False, table="CTX", schema="QA"))
    return {(cell["row"], cell["column"]): cell for cell in result.detail_store.iter_cell_verdicts()}


def test_identifier_column_downgrades_bare_digit_matches() -> None:
    cards = [CreditCardDetector(), ImeiDetector()]
    value = "590849826626661"
    profile = profile_of(value)
    for detector in cards:
        match = detector.contextual_match(detector.match(value, profile, "INTERNAL_CODE"),
                                          "INTERNAL_CODE", value)
        if match:
            assert not match.confirmed, f"{detector.type_key} confirmed in an identifier column"

    cells = _verdicts([["590849826626661", value], ["4111111111111111", "4111111111111111"]],
                      ["INTERNAL_CODE", "CARD_NO"])
    assert cells[(1, "INTERNAL_CODE")]["cell_status"] != STATUS_PII
    assert cells[(1, "CARD_NO")]["cell_status"] == STATUS_PII


def test_structured_values_keep_their_verdict_in_identifier_columns() -> None:
    cells = _verdicts([["1234-567890123"]], ["ORDER_REF"])
    cell = cells[(1, "ORDER_REF")]
    assert cell["cell_status"] == STATUS_PII, "a segmented account layout was flattened to a code"
    assert "BANK_ACCOUNT" in cell["privacy_types"] or "은행계좌번호" in cell["privacy_types"]


def test_ordinary_dates_are_not_birth_dates() -> None:
    detector = BirthDateDetector()
    profile = profile_of("2024-01-15")
    assert detector.match("2024-01-15", profile, "EVENT_DATE") is None
    assert detector.match("2024-01-05T10:00:00", profile_of("2024-01-05T10:00:00"), "LOG_TIME") is None
    # ... but a birth label makes the same date personal.
    confirmed = detector.match("2024-01-15", profile, "DATE_OF_BIRTH")
    assert confirmed and confirmed.confirmed
    embedded = detector.match("出生日期 1985-03-15", profile_of("出生日期 1985-03-15"), "REMARKS")
    assert embedded and embedded.confirmed
    assert embedded.fragment == "1985-03-15"


def test_phone_numbers_are_not_read_as_addresses() -> None:
    detector = AddressDetector()
    value = "전화 010-1234-5678로 문의"
    assert detector.match(value, profile_of(value), "CONTACT_NOTE") is None
    real = "강남구 강남대로 324 5층 501호"
    assert detector.match(real, profile_of(real), "ADDRESS") is not None
    # Road numbers still count when nothing else explains them.
    assert detector.match("Sequoia-ro 121", profile_of("Sequoia-ro 121"), "ADDRESS") is not None


def test_names_in_sentences_are_confirmed_when_anchored() -> None:
    detector = FreeTextNameDetector()
    for value in ("담당자는 홍길동입니다", "고객 성명: 김철수", "홍길동 고객님"):
        match = detector.match(value, profile_of(value), "CONTACT_NOTE")
        assert match and match.confirmed, value
    # A bare predicate without a label or honorific stays for the AI/human pass.
    match = detector.match("홍길동 관련", profile_of("홍길동 관련"), "CONTACT_NOTE")
    assert match is None or not match.confirmed


def test_device_column_keeps_only_the_device_type() -> None:
    """A 15-digit device serial is not also read as a card number."""
    value = "352031091234566"
    raw = CreditCardDetector().match(value, profile_of(value), "IMEI_NO")
    assert raw is not None, "the card shape alone would confirm here"
    downgraded = CreditCardDetector().contextual_match(raw, "IMEI_NO", value)
    assert downgraded is None or not downgraded.confirmed

    cells = _verdicts(
        [["352031091234566", "310100123456789", "1234567", "SN-1234-ABCD"]],
        ["IMEI_NO", "IMSI_NO", "MAC_ADDR", "DEVICE_SN"],
    )
    assert cells[(1, "IMEI_NO")]["cell_status"] == STATUS_PII
    assert set(str(cells[(1, "IMEI_NO")]["privacy_types"]).split(",")) == {"IMEI"}
    assert cells[(1, "IMSI_NO")]["cell_status"] == STATUS_PII
    assert cells[(1, "MAC_ADDR")]["cell_status"] != STATUS_PII
    assert cells[(1, "DEVICE_SN")]["cell_status"] != STATUS_PII


def test_coordinate_shapes_need_a_coordinate_context() -> None:
    from app.detectors.location import LocationDetector

    value = "37.566580"
    hinted = LocationDetector().match(value, profile_of(value), "GPS_LATITUDE")
    unhinted = LocationDetector().match(value, profile_of(value), "UNIT_PRICE")
    assert hinted and hinted.confirmed
    assert unhinted and not unhinted.confirmed

    cells = _verdicts([[value, value], ["3.14159265", "3.14159265"]],
                      ["GPS_LATITUDE", "UNIT_PRICE"])
    assert cells[(1, "GPS_LATITUDE")]["cell_status"] == STATUS_PII
    assert cells[(1, "UNIT_PRICE")]["cell_status"] != STATUS_PII
    assert cells[(2, "UNIT_PRICE")]["cell_status"] != STATUS_PII


def test_every_cell_still_gets_a_verdict_with_context_rules() -> None:
    cells = _verdicts(
        [["1", "5908498266621", "2024-01-15", "전화 010-1234-5678로 문의", "담당자: 홍길동"]],
        ["SEQ_NO", "INTERNAL_CODE", "EVENT_DATE", "NOTE", "OWNER"],
    )
    assert len(cells) == 5
    assert sum(1 for c in cells.values() if c["cell_status"] == STATUS_REVIEW) >= 1
