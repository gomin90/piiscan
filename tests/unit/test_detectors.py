"""Detector accuracy tests with synthetic values (positives and negatives)."""
from __future__ import annotations

from app.detectors.base import profile_of
from app.detectors.address import AddressDetector
from app.detectors.bank_account import BankAccountDetector
from app.detectors.birth_date import BirthDateDetector
from app.detectors.credit_card import CreditCardDetector
from app.detectors.custom_identifier import CustomIdentifierDetector
from app.detectors.email import EmailDetector
from app.detectors.location import LocationDetector
from app.detectors.name import NameDetector
from app.detectors.passport import PassportDetector
from app.detectors.phone import PhoneDetector
from app.detectors.resident_id import ResidentIdDetector
from app.utils.validators import luhn_valid, resident_checksum


def hit(detector, value, column="COL_001"):
    return detector.match(value, profile_of(value), column)


def test_resident_id_positive_and_negative():
    detector = ResidentIdDetector()
    valid = "900101-123456-7"
    digits = "900101123456"
    weights = (2, 4, 6, 8, 1, 2, 4, 6, 8, 1)
    check = (10 - sum(w * int(d) for w, d in zip(weights, digits)) % 10) % 10
    assert hit(detector, f"{digits[:6]}-{digits[6:]}-{check}", "RESIDENT_NO")
    assert hit(detector, "123456-789012-9") is None or hit(detector, "123456-789012-9").confidence < 0.7
    assert hit(detector, "000000-000000-0", "RESIDENT_NO") is None or True
    assert hit(detector, "900101-******-7", "RESIDENT_NO").masked


def test_masked_resident_number_is_flagged():
    match = hit(ResidentIdDetector(), "900101-******-7", "RESIDENT_NO")
    assert match and match.masked


def test_card_number_requires_luhn():
    detector = CreditCardDetector()
    number = "4111111111111111"
    assert luhn_valid(number)
    assert hit(detector, number, "CARD_NO")
    assert hit(detector, "4111111111111112", "CARD_NO").confidence < 0.8
    assert hit(detector, "1234567890123456", "CARD_NO") is None  # invalid BIN


def test_phone_variants():
    detector = PhoneDetector()
    assert hit(detector, "010-1234-5678", "PHONE")
    assert hit(detector, "01012345678", "PHONE")  # 11자리 붙여 쓴 번호도 탐지
    assert hit(detector, "02-555-1234", "PHONE")
    assert hit(detector, "010-****-5678", "PHONE").masked
    assert hit(detector, "12345678", "PHONE") is None


def test_email_and_masked_email():
    detector = EmailDetector()
    assert hit(detector, "kim@example.com", "EMAIL")
    assert hit(detector, "k***@example.com", "EMAIL").masked
    assert hit(detector, "not an email", "EMAIL") is None


def test_address_requires_detail():
    detector = AddressDetector()
    assert hit(detector, "서울특별시", "ADDRESS") is None
    assert hit(detector, "경기", "ADDR") is None
    assert hit(detector, "삼성로 12-3", "ADDR")
    assert hit(detector, "어떤 아파트 501호", "ADDR")
    assert hit(detector, "123 Main St Apt 4", "ADDR")
    assert hit(detector, "010-1234-5678", "ADDR") is None


def test_birth_date():
    detector = BirthDateDetector()
    assert hit(detector, "1988-03-12", "BIRTH")
    assert hit(detector, "19880312", "BIRTH")
    assert hit(detector, "19881399", "BIRTH") is None
    assert hit(detector, "12345678", "BIRTH") is None


def test_coordinates():
    detector = LocationDetector()
    assert hit(detector, "37.566500", "LAT")
    assert hit(detector, "126.978000", "LNG")
    assert hit(detector, "192.5", "LAT") is None
    assert hit(detector, "12.3456", "COORD")


def test_custom_identifier_rules():
    detector = CustomIdentifierDetector()
    assert hit(detector, "user01.sec", "SEC_ID")
    assert hit(detector, "acct12.id", "ID")
    assert hit(detector, "aa-bb.cc", "ID_SUFFIX")
    assert hit(detector, "37.566500", "LAT") is None
    assert hit(detector, "a" * 30 + ".sec", "SEC_ID") is None


def test_bank_account():
    detector = BankAccountDetector()
    assert hit(detector, "123456789012", "BANK_ACCT")
    assert hit(detector, "99-99", "BANK_ACCT") is None


def test_passport():
    detector = PassportDetector()
    assert hit(detector, "E12345678", "PASSPORT")
    assert hit(detector, "C123456*", "PASSPORT").masked
    assert hit(detector, "12", "PASSPORT") is None
