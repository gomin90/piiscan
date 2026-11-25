"""Cell-level gold dataset: structured, unstructured and negative-control cells.

The rule/pipeline harness in tests/qa/dataset.py labels *values*. Cell-level
verdicts need a ground truth that includes column context, natural language and
the deliberate negatives that a detector must not fire on, because "no match"
and "not personal data" are different outcomes in the Cell ledger.

Each row is (column, value, gold_status, gold_types). gold_status is one of
PERSONAL / NOT_PERSONAL; gold_types uses the detector type keys.
"""
from __future__ import annotations

from tests.fixtures.gold_generated import cases as generated_cases

COLUMNS = [
    "SEQ_NO",          # user-declared KEY
    "CUSTOMER_NAME",
    "CONTACT_NOTE",
    "RESIDENT_NO",
    "PHONE",
    "EMAIL",
    "ADDRESS",
    "ACCOUNT_NO",
    "ORDER_REF",
    "INTERNAL_CODE",
    "AMOUNT",
    "EVENT_DATE",
    "SHIP_TO",
    "REMARKS",
    # Low-priority / device / network identifiers, added so every detector in
    # build_detectors() has at least one labelled cell (positive + negative).
    "PASSPORT_NO",
    "DL_NO",
    "IMSI_NO",
    "IMEI_NO",
    "GPS_LAT",
    "GPS_LNG",
    "UUID_VAL",
    "MAC_ADDR",
    "CLIENT_IP",
    "POSTCODE",
    "DEVICE_SN",
    # Policy/2순위 columns added for the 2026 classification: these exercise the
    # context detectors and the natural-language cases from the spec.
    "PASSWORD",
    "CI",
    "CELL_ID",
    "RFID_TAG",
    "SSID",
    "ACCOUNT_GUID",
    "FAX_NO",
    "MARRIAGE_ANNIV",
    "CHILD_COUNT",
    "MASKED_FIELD",
    "SYSTEM_ID",
    "PROC_CODE",
    "CIRCLE_NO",
    "APP_USER",
    "CART_TOTAL",
    "SPIN_VALUE",
]

# (column, value, gold status, gold types)
CASES: list[tuple[str, str, str, tuple[str, ...]]] = [
    # --- structured personal data -------------------------------------------------
    ("CUSTOMER_NAME", "홍길동", "PERSONAL", ("NAME",)),
    ("CUSTOMER_NAME", "김철수", "PERSONAL", ("NAME",)),
    ("CUSTOMER_NAME", "이영순", "PERSONAL", ("NAME",)),
    ("CUSTOMER_NAME", "John Smith", "PERSONAL", ("NAME",)),
    ("CONTACT_NOTE", "담당자는 홍길동입니다", "PERSONAL", ("NAME",)),
    ("CONTACT_NOTE", "고객 성명: 김철수", "PERSONAL", ("NAME",)),
    ("CONTACT_NOTE", "홍길동 고객님", "PERSONAL", ("NAME",)),
    ("CONTACT_NOTE", "kim.min.jun@example.com로 연락", "PERSONAL", ("EMAIL",)),
    ("CONTACT_NOTE", "전화 010-1234-5678로 문의", "PERSONAL", ("PHONE_NUMBER",)),
    ("RESIDENT_NO", "900101-1234564", "PERSONAL", ("RESIDENT_ID",)),
    ("RESIDENT_NO", "9001011234564", "PERSONAL", ("RESIDENT_ID",)),
    ("PHONE", "010-1234-5678", "PERSONAL", ("PHONE_NUMBER",)),
    ("PHONE", "+82-2-555-1234", "PERSONAL", ("PHONE_NUMBER",)),
    ("EMAIL", "min.kim@example.com", "PERSONAL", ("EMAIL",)),
    ("EMAIL", "hong.gildong@company.co.kr", "PERSONAL", ("EMAIL",)),
    ("ADDRESS", "서울특별시 강남구 Sequoia-ro 54", "PERSONAL", ("ADDRESS",)),
    ("ADDRESS", "제주특별자치도 제주의호물길 324", "PERSONAL", ("ADDRESS",)),
    ("ADDRESS", "강남구 강남대로 324 5층 501호", "PERSONAL", ("ADDRESS",)),
    ("SHIP_TO", "123 Main St, Springfield, IL 62703", "PERSONAL", ("ADDRESS",)),
    ("ACCOUNT_NO", "110012345679", "PERSONAL", ("BANK_ACCOUNT",)),
    ("REMARKS", "카드 4111-1111-1111-1111로 결제", "PERSONAL", ("CARD_NUMBER",)),
    ("REMARKS", "出生日期 1985-03-15", "PERSONAL", ("BIRTH_DATE",)),

    # --- negative controls (must NOT be personal data) --------------------------
    ("ORDER_REF", "ORD-2024-000123", "NOT_PERSONAL", ()),
    ("ORDER_REF", "TXN987654321", "NOT_PERSONAL", ()),
    ("INTERNAL_CODE", "SYS-4482-A", "NOT_PERSONAL", ()),
    ("INTERNAL_CODE", "987654321012345", "NOT_PERSONAL", ()),
    ("AMOUNT", "15000", "NOT_PERSONAL", ()),
    ("AMOUNT", "1234.56", "NOT_PERSONAL", ()),
    ("EVENT_DATE", "2024-01-15", "NOT_PERSONAL", ()),
    ("INTERNAL_CODE", "590849826626661", "NOT_PERSONAL", ()),
    ("INTERNAL_CODE", "900101-1234567", "NOT_PERSONAL", ()),   # masked/foreign format
    ("INTERNAL_CODE", "1234567890123456", "NOT_PERSONAL", ()),
    ("INTERNAL_CODE", "b12345678901", "NOT_PERSONAL", ()),
    ("INTERNAL_CODE", "47.98, 1.10", "NOT_PERSONAL", ()),
    ("INTERNAL_CODE", "123.456.789.012", "NOT_PERSONAL", ()),
    ("INTERNAL_CODE", "512399106258033", "NOT_PERSONAL", ()),
    ("INTERNAL_CODE", "8081819869245", "NOT_PERSONAL", ()),

    # --- masked / protected columns ---------------------------------------------
    ("REMARKS", "HASH:a1b2c3d4e5f6", "NOT_PERSONAL", ()),
    ("REMARKS", "MASK:9001**-******", "NOT_PERSONAL", ()),
    ("REMARKS", "ENC:U2VjcmV0RGF0YQ==", "NOT_PERSONAL", ()),

    # --- empty and ambiguous ------------------------------------------------------
    ("REMARKS", "", "EMPTY", ()),
    ("CONTACT_NOTE", "", "EMPTY", ()),
    ("ADDRESS", "-", "NOT_PERSONAL", ()),
    ("CUSTOMER_NAME", "N/A", "NOT_PERSONAL", ()),
    ("CONTACT_NOTE", "주문 500건 처리 완료", "NOT_PERSONAL", ()),
    ("CONTACT_NOTE", "시스템 ID 20240115-0001", "NOT_PERSONAL", ()),
    ("REMARKS", "결제 수단: 카드 4번", "NOT_PERSONAL", ()),
    ("REMARKS", "고객 1234567890123456789012345678", "NOT_PERSONAL", ()),
    ("SHIP_TO", "창고 B-12, 3층", "NOT_PERSONAL", ()),
    ("CONTACT_NOTE", "배송 완료 (2024-03-01)", "NOT_PERSONAL", ()),
    ("REMARKS", "배송 요청: 010-9876-5432", "PERSONAL", ("PHONE_NUMBER",)),
    ("REMARKS", "No personal data here, just a product code P-9981", "NOT_PERSONAL", ()),

    # --- device / network / low-priority identifiers -----------------------------
    ("PASSPORT_NO", "E12345678", "PERSONAL", ("PASSPORT",)),
    ("PASSPORT_NO", "M1234567", "PERSONAL", ("PASSPORT",)),
    ("PASSPORT_NO", "E12**456", "PERSONAL", ("PASSPORT",)),
    ("DL_NO", "20211234-56", "PERSONAL", ("DRIVER_LICENSE",)),
    ("DL_NO", "2021123456", "PERSONAL", ("DRIVER_LICENSE",)),
    ("IMSI_NO", "310100123456789", "PERSONAL", ("IMSI",)),
    ("IMEI_NO", "352031091234566", "PERSONAL", ("IMEI",)),
    ("GPS_LAT", "37.566580", "PERSONAL", ("LATITUDE",)),
    ("GPS_LNG", "126.978194", "PERSONAL", ("LONGITUDE",)),
    ("UUID_VAL", "123e4567-e89b-12d3-a456-426614230000", "PERSONAL", ("UUID",)),
    ("MAC_ADDR", "00-1A-2B-3C-4D-5E", "PERSONAL", ("MAC",)),
    ("CLIENT_IP", "211.233.10.45", "PERSONAL", ("IP",)),
    ("POSTCODE", "04512", "PERSONAL", ("POSTCODE",)),
    ("DEVICE_SN", "SN-1234-ABCD", "NOT_PERSONAL", ()),     # device serial: identifier, not personal data

    # --- look-alikes for the same types (must NOT be personal data) --------------
    ("PASSPORT_NO", "A1234", "NOT_PERSONAL", ()),          # too short for any passport
    ("DL_NO", "AB12345 6", "NOT_PERSONAL", ()),            # broken licence layout
    ("IMSI_NO", "31010012345678X", "NOT_PERSONAL", ()),    # non-numeric subscriber id
    ("IMEI_NO", "35203109123456", "NOT_PERSONAL", ()),     # 14 digits: not an IMEI
    ("GPS_LAT", "91.0", "NOT_PERSONAL", ()),               # outside latitude range
    ("GPS_LNG", "181.5", "NOT_PERSONAL", ()),              # outside longitude range
    ("INTERNAL_CODE", "3.14159", "NOT_PERSONAL", ()),      # a plain decimal, no coordinate hint
    ("UUID_VAL", "123456789012345678901234567890ab", "NOT_PERSONAL", ()),
    ("MAC_ADDR", "1234567", "NOT_PERSONAL", ()),           # neither MAC nor serial
    ("CLIENT_IP", "256.1.1.1", "NOT_PERSONAL", ()),        # invalid IPv4 octet
    ("POSTCODE", "1234567890", "NOT_PERSONAL", ()),        # ten digits: a phone-like run
    ("DEVICE_SN", "0000", "NOT_PERSONAL", ()),             # no letters, no segments

    # --- 1순위 / 2순위 policy columns -------------------------------------------------
    ("PASSWORD", "123456", "PERSONAL", ("AUTH_CREDENTIAL",)),
    ("PASSWORD", "Pa$$w0rd!QuerY", "PERSONAL", ("AUTH_CREDENTIAL",)),
    ("PASSWORD", "0f4c2b9d8e6a1f5b3c7d9e2a4b6c8d0f", "PERSONAL", ("AUTH_CREDENTIAL",)),
    ("CI", "a1b2c3d4e5f6789012345678", "PERSONAL", ("CI",)),
    ("CI", "00000000000000000000", "PERSONAL", ("CI",)),
    ("CELL_ID", "01012345678", "PERSONAL", ("CELL_ID",)),
    ("CELL_ID", "010-1234-5678", "PERSONAL", ("CELL_ID", "PHONE_NUMBER")),
    ("RFID_TAG", "E280123456789ABC", "PERSONAL", ("RFID",)),
    ("SSID", "Office-Wi-Fi-5G", "PERSONAL", ("WIRELESS_NETWORK",)),
    ("ACCOUNT_GUID", "11013810000000000000000000000001", "PERSONAL", ("ACCOUNT_GUID",)),
    ("FAX_NO", "02-555-1234", "PERSONAL", ("FAX_NUMBER",)),
    ("MARRIAGE_ANNIV", "2024-04-02", "PERSONAL", ("PERSONAL_ATTRIBUTE",)),
    ("CHILD_COUNT", "2", "PERSONAL", ("PERSONAL_ATTRIBUTE",)),

    # --- masking canaries: a masked value must never be cleared, but is not
    #     a confirmed raw identifier either (spec §12.3 / §12.4) --------------------
    ("MASKED_FIELD", "***1234***", "MASKED_PERSONAL", ("MASKED",)),
    ("MASKED_FIELD", "abcd****5678", "MASKED_PERSONAL", ("MASKED",)),
    ("MASKED_FIELD", "1234****5678", "MASKED_PERSONAL", ("MASKED",)),

    # --- false-positive traps from spec §12.4 --------------------------------------
    ("SYSTEM_ID", "00000000000000000000000000000001", "NOT_PERSONAL", ()),
    ("PROC_CODE", "A1000000000000", "NOT_PERSONAL", ()),
    ("CIRCLE_NO", "98765432109876543210", "NOT_PERSONAL", ()),
    ("APP_USER", "AP-2026-0001", "NOT_PERSONAL", ()),
    ("CART_TOTAL", "1234567", "NOT_PERSONAL", ()),
    ("SPIN_VALUE", "PIN-0000", "NOT_PERSONAL", ()),

    # --- natural language (spec §12.2) ----------------------------------------------
    ("CONTACT_NOTE", "고객 홍길동의 전화번호는 010-1234-5678이며 이메일은 kim@example.com입니다",
     "PERSONAL", ("NAME", "PHONE_NUMBER", "EMAIL")),
    ("CONTACT_NOTE", "김철수의 계좌 1234567890123 payment", "PERSONAL", ("NAME", "BANK_ACCOUNT")),
    ("REMARKS", "Patient John Smith born 1985-01-01", "PERSONAL", ("NAME", "BIRTH_DATE")),
    ("REMARKS", "Mac address AA:BB:CC:DD:EE:FF", "PERSONAL", ("MAC",)),
    ("REMARKS", "Latitude 37.5665 Longitude 126.9780", "PERSONAL", ("LATITUDE", "LONGITUDE")),
    ("REMARKS", "전화 010-1234-5678 / 이메일 abc@def.com", "PERSONAL", ("PHONE_NUMBER", "EMAIL")),
    ("REMARKS", "order 12345 shipped to 123 Main Street", "PERSONAL", ("ADDRESS",)),
    ("REMARKS", "IMSI 310100123456789", "PERSONAL", ("IMSI",)),

    # --- multi-occurrence cells (spec §6) -------------------------------------------
    ("PHONE", "010-1111-2222, 010-3333-4444", "PERSONAL", ("PHONE_NUMBER",)),
    ("EMAIL", "alice@example.com; bob@example.org", "PERSONAL", ("EMAIL",)),
    ("CONTACT_NOTE", "홍길동 010-1234-5678 kim@example.com", "PERSONAL",
     ("NAME", "PHONE_NUMBER", "EMAIL")),
    ("INTERNAL_CODE", "{\"user_name\":\"Kim\",\"user_email\":\"kim@test.com\"}", "PERSONAL",
     ("EMAIL",)),
]


ALL_CASES = CASES + generated_cases()
ALL_COLUMNS = list(dict.fromkeys(COLUMNS + [case[0] for case in ALL_CASES]))


def build_csv(path) -> list[tuple[int, str]]:
    """Write the gold CSV and return (row_number, column) gold labels."""
    from collections import defaultdict
    rows: dict[int, dict[str, str]] = defaultdict(dict)
    types: dict[tuple[int, str], tuple[str, tuple[str, ...]]] = {}
    for index, (column, value, status, gold_types) in enumerate(ALL_CASES, start=1):
        # Every case occupies one real CSV row: the KEY column carries the case
        # id so a row that only holds an empty cell is not skipped by the loader.
        rows[index][COLUMNS[0]] = str(index)
        rows[index][column] = value
        types[(index, column)] = (status, gold_types)
    lines = [",".join(ALL_COLUMNS)]
    for row_number in range(1, len(rows) + 1):
        cells = []
        for column in ALL_COLUMNS:
            value = rows[row_number].get(column, "")
            cells.append('"' + value.replace('"', '""') + '"' if value else "")
        lines.append(",".join(cells))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return [(row, column) for (row, column) in types.keys()]


def gold_labels() -> dict[tuple[int, str], tuple[str, tuple[str, ...]]]:
    """(row, column) -> (gold status, gold types). All other cells are empty."""
    return {(row, column): (status, kinds)
            for row, (column, value, status, kinds) in enumerate(ALL_CASES, start=1)
            if value}
