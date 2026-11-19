"""Ground-truth synthetic dataset for the independent QA harness.

Every value is labelled with the single privacy type it is *intended* to be
(None = not personal data). Values whose surface form is genuinely ambiguous
(15-digit IMSI vs IMEI, 12-digit numbers, decimals inside the lat/lon range)
are labelled with the meaning the fixture is supposed to carry, so any other
detector that also fires on that value is counted as a false positive.

Checksum-valid identifiers are built from first principles (standard Korean
resident-number weights, Luhn, bank weights), independently of the detection
code, so ground truth does not depend on the implementation.
"""
from __future__ import annotations

RESIDENT_WEIGHTS = (2, 4, 6, 8, 1, 2, 4, 6, 8, 1, 2, 4)
BANK_WEIGHTS = (2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4)


def luhn_check_digit(body: str) -> int:
    total = 0
    for index, ch in enumerate(reversed(body)):
        digit = int(ch)
        if index % 2 == 0:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return (10 - total % 10) % 10


def resident(birth: str, seq: str) -> str:
    """Checksum-valid 13-digit Korean resident registration number."""
    body = birth + seq
    total = sum(w * int(d) for w, d in zip(RESIDENT_WEIGHTS, body))
    return body + str((10 - total % 10) % 10)


def bank_account(body: str) -> str:
    total = sum(w * int(d) for w, d in zip(BANK_WEIGHTS, body))
    return body + str((10 - total % 10) % 10)


def imei(prefix: str) -> str:
    return prefix + str(luhn_check_digit(prefix))


# (value, intended_type, other_types_acceptable_as_true, why)
DATASET: list[tuple[str, str | None, tuple[str, ...], str]] = []


def add(value: str, truth: str | None, note: str, also_ok: tuple[str, ...] = ()) -> None:
    DATASET.append((value, truth, also_ok, note))


# --- 주민등록번호 -----------------------------------------------------------
add(resident("900101", "123456"), "RESIDENT_ID", "valid checksum")
add(f"{resident('850312', '987654')[:6]}-{resident('850312', '987654')[6:12]}-{resident('850312', '987654')[12]}",
    "RESIDENT_ID", "valid checksum, dash separated")
add("900101-123456-1", "RESIDENT_ID", "13자, 체크섬 오류 ( malformed real record )")
add("900101-******-7", "RESIDENT_ID", "masked resident number")
add("900101123456", None, "12자 numeric: 13자 주민번호 아님 (ambiguity probe)",
    {"FOREIGNER_ID", "INSURANCE_NUMBER", "BANK_ACCOUNT"})
add("850312456789", "FOREIGNER_ID", "12자 외국인등록번호 (ambiguity probe)", ("RESIDENT_ID", "INSURANCE_NUMBER"))
add("730520654321", "INSURANCE_NUMBER", "12자 보험번호 (ambiguity probe)", ("RESIDENT_ID", "FOREIGNER_ID"))

# --- 여권번호 ---------------------------------------------------------------
add("E12345678", "PASSPORT", "KR passport E + 8 digits")
add("D01234567", "PASSPORT", "KR passport D + 8 digits")
add("A12345678", "PASSPORT", "generic passport pattern")
add("E12***78", "PASSPORT", "masked passport number")
add("P123456789012", None, "13자 -> 여권번호 길이 초과")

# --- 운전면허번호 -----------------------------------------------------------
add("20151234-56", "DRIVER_LICENSE", "8-2 국내면허")
add("2020123456", "DRIVER_LICENSE", "10자, 발급년 유효")
add("2015****-56", "DRIVER_LICENSE", "masked licence")
add("A123456789", None, "구형 1letter+9digits: 컬럼 근거 없으면 ambiguity", {"DRIVER_LICENSE"})
add("1234567890", None, "10자 numeric: SN/계좌 ambiguity", {"SN", "BANK_ACCOUNT"})

# --- 사회보장번호 -----------------------------------------------------------
add("123-45-6789", "SOCIAL_SECURITY", "US SSN")
add("123-**-6789", "SOCIAL_SECURITY", "masked SSN")
add("000-45-6789", None, "area 000은 유효하지 않음")

# --- 국가보험/의료 ----------------------------------------------------------
add("12-345678-9012", "INSURANCE_NUMBER", "NHI dash pattern")
add("12-345678-****", "INSURANCE_NUMBER", "masked insurance number")

# --- 카드번호 ---------------------------------------------------------------
add("4111111111111111", "CARD_NUMBER", "Visa BIN, Luhn valid")
add("5500000000000004", "CARD_NUMBER", "Mastercard BIN, Luhn valid")
add("370000000000000", "CARD_NUMBER", "Amex 15-digit")
add("4111-12345-XXXXXX", "CARD_NUMBER", "masked card")
add("4111123456789012", "CARD_NUMBER", "Visa BIN, Luhn invalid (오타)")
add("1234567890123456", None, "BIN 1 -> 카드번호 아님")
add("9780306406157", None, "ISBN-13 (BIN 9)")

# --- 은행계좌 ---------------------------------------------------------------
add(bank_account("10000123456"), "BANK_ACCOUNT", "12자, 은행 체크섬 valid")
add("1234-567890-1234", "BANK_ACCOUNT", "segmented account")
add("123-4567-****", "BANK_ACCOUNT", "masked account")

# --- 이메일 ---------------------------------------------------------------
add("hong@example.com", "EMAIL", "plain email")
add("kim.joseph@company.co.kr", "EMAIL", "corporate email")
add("ho***@example.com", "EMAIL", "masked email")
add("not-an-email.com", None, "domain only")
add("user@", None, "no domain")
add("a@b", None, "no TLD")

# --- 전화번호 ---------------------------------------------------------------
add("010-1234-5678", "PHONE_NUMBER", "KR mobile")
add("01012345678", "PHONE_NUMBER", "KR mobile, no separator")
add("02-555-1234", "PHONE_NUMBER", "KR landline")
add("+1-202-555-0123", "PHONE_NUMBER", "international number")
add("010-****-5678", "PHONE_NUMBER", "masked mobile")
add("010-12-345678", None, "잘못된 Telephone 구분자")

# --- 이름 ---------------------------------------------------------------
add("김민준", "NAME", "Korean name")
add("홍길동", "NAME", "Korean name")
add("John Smith", "NAME", "latin full name")
add("박**", "NAME", "masked Korean name")
add("J***", "NAME", "masked latin name")
add("서울시", None, "region token, not a name")
add("프로그래밍", None, "common noun")
add("product name", None, "generic label")

# --- 상세주소 ------------------------------------------------------------
add("서울시 강남구 강남대로 154-12", "ADDRESS", "road name + number")
add("부산광역시 해운대구 중로 5", "ADDRESS", "road name + number")
add("테스트로 254-49 apartments 15호", "ADDRESS", "mixed address")
add("Seoul 123 Main St apt 4", "ADDRESS", "latin address")
add("서울", None, "city only")
add("no digits here", None, "text without detail")

# --- 생년월일 ------------------------------------------------------------
add("1990-01-01", "BIRTH_DATE", "ISO date")
add("19851113", "BIRTH_DATE", "compact YYYYMMDD")
add("1985/11/13", "BIRTH_DATE", "slash date")
add("900101", "BIRTH_DATE", "YYMMDD")
add("1990-01-**", "BIRTH_DATE", "masked date")
add("20240230", None, "Feb 30 does not exist")
add("1990-13-01", None, "month 13")
add("123456", None, "YYMMDD month 34")

# --- 좌표 ---------------------------------------------------------------
add("37.566520", "LOCATION", "latitude value")
add("-122.419416", "LOCATION", "longitude value")
add("37.5665", "LOCATION", "4 decimal precision")
add("37.5", None, "2 decimals, no column hint")
add("3.1416", None, "generic decimal inside lat range")
add("1000.1234", None, "outside coordinate range")

# --- IMSI / IMEI -----------------------------------------------------------
add("450123456789012", "IMSI", "Korean MCC 450")
add("310000000000007", "IMSI", "US MCC 310")
add(imei("35200000000000"), "IMEI", "15자, Luhn valid")
add(imei("86972001234567"), "IMEI", "15자, Luhn valid")
add("123456789012345", None, "MCC 123 invalid", ())

# --- UUID / MAC / IP / SN / Postcode --------------------------------------
add("123e4567-e89b-12d3-a456-426614174000", "UUID", "UUID v4")
add("00112233-4455-6677-8899-aabbccddeeff", "UUID", "UUID-like")
add("00:1A:2B:3C:4D:5E", "MAC", "MAC colon form")
add("00-1A-2B-3C-4D-5E", "MAC", "MAC dash form")
add("00:1A:2B:3C:4D", None, "incomplete MAC")
add("192.168.0.101", "IP", "IPv4 private")
add("203.151.0.1", "IP", "IPv4 public")
add("fe80::1f42", "IP", "IPv6 link-local")
add("256.1.1.1", None, "octet out of range")
add("AB12CD34", "SN", "serial number")
add("X9021Z77", "SN", "serial number")
add("SN-1234-5678", "SN", "hyphenated serial")
add("06026", "POSTCODE", "KR 5-digit postcode")
add("123-456", "POSTCODE", "KR 3+3 postcode")
add("SW1 1AA", "POSTCODE", "UK postcode")
add("1234", None, "4-digit number")

# --- 사용자 정의 ID ---------------------------------------------------------
add("user1.sec", "CUSTOM_SEC_ID", "custom .sec rule")
add("acct0.id", "CUSTOM_ID_SUFFIX", "custom .id rule")
add("ab1.cd2", "CUSTOM_WILDCARD_ID", "wildcard rule in custom_patterns.json")
add("report.pdf", None, "file name (wildcard FP probe)")
add("data.json", None, "file name (wildcard FP probe)")


# Protection-column fixture: (column, values) used by the MASK/HASH/ENC test.
PROTECTION_COLUMNS: list[tuple[str, list[str]]] = [
    ("USER_NAME", ["김민준", "홍길동", "John Smith"]),
    ("USER_NAME_MASK", ["김**", "홍**", "J***"]),
    ("USER_ID_HASH", ["e3b0c44298fc1c149afbf4c8996fb924",
                      "2c26b46b68ffc68ff99b453c1d30413413422d706483b52b226be749bd4b90c0"]),
    ("USER_NAME_ENC", ["U2VjcmV0IFZhbHVlMTIzNDU2Nzg=", "c29tZQ=="]),
    ("PHONE_SHA256", ["9f8b4c4d4c4e5f6a7b8c9d0e1f2a3b4c5d6e7f80"]),
    ("GENDER_CODE", ["M", "F", "M"]),
]

CODE_GROUP_COLUMNS: list[tuple[str, list[str]]] = [
    ("GROUP_CODE", ["G001", "G001", "G002", "G002"]),
    ("CODE", ["100001", "100002", "200001", "200002"]),
    ("CODE_NAME", [" male", " female", " blood A", " blood B"]),
    ("GROUP_DESC", ["성별 코드", "성별 코드", "진료 코드", "진료 코드"]),
    ("GENDER_CODE", ["M", "F", "M", "F"]),
]
