"""Policy layer, 1순위 guard, new context detectors and multi-occurrence matching.

These tests pin the behaviour the improvement spec asks for:

* the JSON policy is the single source of tier / sensitivity / aliases;
* a column that the policy calls 1순위 is never cleared as 비개인정보 just
  because its value looks like a hash or an integer;
* the new context types (PASSWORD, CI, CELL_ID, RFID, SSID, FAX, biometric,
  account GUID, personal attributes) are detected as candidates;
* one cell can hold several occurrences and each keeps its own offset;
* the physical source line of every logical record is tracked.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config.privacy_policy import get_policy, load_policy
from app.core.analysis_service import analyse_file
from app.core.cell_verdict import rule_negative_verdict, STATUS_NON, STATUS_UNRATED
from app.core.csv_loader import CsvLoader
from app.core.csv_loader import CsvLoader, FileSettings
from app.detectors.base import ScanContext, profile_of
from app.detectors.context_policy import (
    AccountGuidDetector,
    AuthCredentialDetector,
    CellIdDetector,
    CustomerLinkageDetector,
    FaceBiometricDetector,
    FaxNumberDetector,
    PersonalAttributeDetector,
    RfidDetector,
    WirelessNetworkDetector,
)
from app.detectors.credit_card import CreditCardDetector
from app.detectors.email import EmailDetector
from app.detectors.phone import PhoneDetector
from app.detectors.resident_id import ResidentIdDetector
from app.detectors.name import NameDetector


# ---------------------------------------------------------------- policy file
def test_policy_file_is_valid_and_complete():
    path = Path("resources/rules/privacy_kr_2026.json")
    assert path.exists(), "privacy policy file must ship with the app"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["policy_version"]
    policy = get_policy()
    for code in ("RESIDENT_ID", "PASSPORT", "DRIVER_LICENSE", "NAME", "EMAIL", "PHONE_NUMBER",
                 "ADDRESS", "BIRTH_DATE", "CARD_NUMBER", "BANK_ACCOUNT", "LOCATION",
                 "CELL_ID", "RFID", "WIRELESS_NETWORK", "AUTH_CREDENTIAL", "CI", "BIOMETRIC",
                 "PERSONAL_ATTRIBUTE", "ACCOUNT_GUID", "GAID", "SN", "UUID", "MAC", "IP",
                 "POSTCODE", "IMEI", "IMSI"):
        assert policy.get(code) is not None, f"{code} missing from the policy"
        assert policy.tier(code) in (1, 2)
        assert 0.0 <= policy.sensitivity(code) <= 1.0


def test_policy_falls_back_when_the_file_is_broken(tmp_path: Path):
    bogus = tmp_path / "broken.json"
    bogus.write_text("{not json", encoding="utf-8")
    policy = load_policy(bogus)
    assert policy.source in ("fallback", "builtin")
    assert policy.warnings, "a broken policy file must record why it fell back"
    assert policy.tier("RESIDENT_ID") == 1


def test_tier_is_not_risk_or_confidence():
    policy = get_policy()
    # Tier is priority, not automatic risk: a low-tier type keeps its own
    # sensitivity, and the two numbers are reported separately.
    assert policy.tier("UUID") == 2
    assert policy.sensitivity("UUID") < policy.sensitivity("RESIDENT_ID")
    for entry in policy.coverage():
        assert "tier" in entry and "sensitivity" in entry and "method" in entry


# ------------------------------------------------------ 1순위 negative guard (§2)
@pytest.mark.parametrize("column, value", [
    ("PASSWORD", "0f4c2b9d8e6a1f5b3c7d9e2a4b6c8d0f"),
    ("password_hash", "a" * 64),
    ("PIN", "4821"),
    ("CI", "000123"),
    ("FACE_TEMPLATE", "123456789"),
    ("CELL_ID", "20240101"),
    ("SSID", "12345"),
])
def test_high_priority_columns_are_never_cleared_by_shape(column: str, value: str):
    assert rule_negative_verdict(value, column) is None, (
        f"{column} holding {value!r} must not be auto-cleared as 비개인정보"
    )


@pytest.mark.parametrize("column, value", [
    ("ORDER_STATUS", "42"),
    ("PRICE", "19.99"),
    ("ACTIVE_FLAG", "true"),
    ("COLOR", "#ff0000"),
])
def test_ordinary_columns_can_still_be_cleared(column: str, value: str):
    verdict = rule_negative_verdict(value, column)
    assert verdict is not None, f"{column}={value!r} should be cleared as non-personal"
    assert verdict[0] != STATUS_NON  # a label, not a bare status


def test_guard_keeps_cell_unrated_end_to_end(tmp_path: Path):
    path = tmp_path / "guard.csv"
    path.write_text(
        "MEMBER_ID,PASSWORD,ORDER_STATUS,CI\n"
        "1,0f4c2b9d8e6a1f5b3c7d9e2a4b6c8d0f,42,000123\n",
        encoding="utf-8",
    )
    result = analyse_file(path)
    store = result.detail_store
    statuses = {cell["column"]: cell["cell_status"] for cell in store.iter_cell_verdicts()}
    # The guard's promise is "never auto-cleared"; the cell may be settled as a
    # review candidate by a context detector, but it is never 비개인정보.
    assert statuses["PASSWORD"] != STATUS_NON and statuses["CI"] != STATUS_NON
    assert statuses["ORDER_STATUS"] == STATUS_NON
    cell = next(c for c in store.iter_cell_verdicts() if c["column"] == "PASSWORD")
    assert cell["tier"] == 1, "a 1순위 column must carry tier 1 on its cell record"


# ------------------------------------------------------- new context detectors (§3)
def test_password_column_value_is_a_candidate():
    match = AuthCredentialDetector().find_matches("123456", profile_of("123456"), "PASSWORD")[0]
    assert match.type_key == "AUTH_CREDENTIAL"
    assert match.confidence < 0.90, "a bare password value is a candidate, not proof"


def test_ci_column_short_value_is_not_confirmed():
    matches = CustomerLinkageDetector().find_matches("CI123", profile_of("CI123"), "CI")
    assert not matches or not matches[0].confirmed


def test_ci_column_long_value_is_confirmed():
    value = "a1b2c3d4e5f6789012345678"
    match = CustomerLinkageDetector().find_matches(value, profile_of(value), "CI")[0]
    assert match.confirmed and match.tier == 1


def test_cell_id_rfid_ssid_fax_and_attribute_are_detected():
    assert CellIdDetector().find_matches("01012345678", profile_of("01012345678"), "CELL_ID")[0]
    assert RfidDetector().find_matches("E280123456789ABC", profile_of("E280123456789ABC"), "RFID_TAG")[0]
    assert WirelessNetworkDetector().find_matches("Office-Wi-Fi-5G", profile_of("Office-Wi-Fi-5G"), "SSID")[0]
    assert FaxNumberDetector().find_matches("02-555-1234", profile_of("02-555-1234"), "FAX")[0]
    assert PersonalAttributeDetector().find_matches("2", profile_of("2"), "CHILD_COUNT")[0]


def test_biometric_token_is_detected_as_tier_1():
    match = FaceBiometricDetector().find_matches("9f8a7b6c5d4e3f2a1b0c",
                                                profile_of("9f8a7b6c5d4e3f2a1b0c"), "FACE_TEMPLATE")[0]
    assert match.type_key == "BIOMETRIC" and match.tier == 1


def test_account_guid_is_detected():
    value = "12345678901234567890123456789012"
    match = AccountGuidDetector().find_matches(value, profile_of(value), "GAID")[0]
    assert match.type_key in ("ACCOUNT_GUID", "GAID")


def test_name_is_not_confirmed_for_product_or_department():
    detector = NameDetector()
    product = detector.find_matches("비스포크 오디오", profile_of("비스포크 오디오"), "GOODS_GROUP")
    assert not any(m.confirmed for m in product)
    person = detector.find_matches("홍길동", profile_of("홍길동"), "CUSTOMER_NAME")
    assert person and person[0].confirmed


# ------------------------------------------------ multi-occurrence cells (§6)
def test_three_phone_numbers_in_one_cell():
    value = "010-1111-2222 / 010-3333-4444 / 010-5555-6666"
    matches = PhoneDetector().find_matches(value, profile_of(value), "PHONE_LIST")
    assert len(matches) == 3
    assert [m.occurrence_index for m in matches] == [1, 2, 3]
    assert all(m.match_start >= 0 and m.match_end > m.match_start for m in matches)
    assert matches[0].match_end <= matches[1].match_start


def test_two_emails_and_two_cards():
    emails = EmailDetector().find_matches("a@x.com,b@y.com", profile_of("a@x.com,b@y.com"), "EMAILS")
    assert len(emails) == 2
    cards = CreditCardDetector().find_matches(
        "4111111111111111 5500000000000004", profile_of("4111111111111111 5500000000000004"), "PAN")
    assert len(cards) == 2


def test_resident_number_and_phone_in_one_cell():
    value = "900101-1234567 / 010-1234-5678"
    hits = ResidentIdDetector().find_matches(value, profile_of(value), "CONTACT")
    assert len(hits) == 1 and hits[0].match_start == 0
    phones = PhoneDetector().find_matches(value, profile_of(value), "CONTACT")
    assert len(phones) == 1 and phones[0].match_start > 0


def test_json_cell_and_comma_address_are_counted():
    cell = '{"email":"kim@example.com","phone":"010-1234-5678"}'
    emails = EmailDetector().find_matches(cell, profile_of(cell), "PROFILE_JSON")
    phones = PhoneDetector().find_matches(cell, profile_of(cell), "PROFILE_JSON")
    assert emails and phones

    address = "서울시 마포구, 스포츠 strada 20, 101호"
    hits = [m for m in EmailDetector().find_matches(address, profile_of(address), "ADDR") if m]
    assert not hits  # an address is not an e-mail merely because of the commas


# ------------------------------------------------ source-line tracking (§5)
def test_physical_lines_are_tracked_with_quoted_multiline_records(tmp_path: Path):
    path = tmp_path / "lines.csv"
    path.write_text(
        "id,name\n"
        "1,\"multi\nline\"\n"
        "\n"
        "2,single\n",
        encoding="utf-8",
    )
    loader = CsvLoader(FileSettings(path=str(path), encoding="utf-8", delimiter=",",
                                    has_header=True, table="lines"))
    loader.prepare()
    spans = list(loader.iter_rows())
    assert [row[0] for row in spans] == [0, 1], "blank lines must not create records"
    assert spans[0][2:] == (2, 3), "the quoted record spans physical lines 2-3"
    assert spans[1][2:] == (5, 5)


def test_report_rows_carry_the_source_line(tmp_path: Path):
    path = tmp_path / "src.csv"
    path.write_text(
        "id,name\n"
        "1,\"김\n길동\"\n"
        "\n"
        "2,홍길동\n",
        encoding="utf-8",
    )
    result = analyse_file(path)
    rows = list(result.detail_store.iter_rows())
    assert rows[0]["line_start"] == 2 and rows[0]["line_end"] == 3
    assert rows[1]["line_start"] == 5 and rows[1]["line_end"] == 5
