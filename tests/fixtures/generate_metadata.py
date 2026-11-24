"""Synthetic DB metadata extracts for Function B tests and benchmarks."""
from __future__ import annotations

import csv
import random
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parent

TABLES = [
    ("CUSTOMER", "고객 기본 정보", [
        ("CUST_NO", "고객 번호", "NUMBER", 10, True, True, False),
        ("CUST_NAME", "고객 성명", "VARCHAR", 50, False, False, False),
        ("CUST_EMAIL", "이메일 주소", "VARCHAR", 100, False, False, False),
        ("MOBILE_NO", "휴대폰 번호", "VARCHAR", 11, False, False, False),
        ("ADDR_TEXT", "도로명 주소", "VARCHAR", 200, True, False, False),
        ("BIRTH_DATE", "생년월일", "DATE", None, True, False, False),
        ("REG_NO", "주민등록번호", "VARCHAR", 13, False, False, False),
        ("POINT_AMT", "포인트 잔액", "NUMBER", 12, False, False, False),
        ("CUST_STATUS_CD", "고객 상태 코드", "CHAR", 2, False, False, False),
    ]),
    ("ORDER_HEAD", "주문 헤더", [
        ("ORDER_ID", "주문 ID", "BIGINT", None, False, True, False),
        ("CUST_NO", "고객 번호", "NUMBER", 10, False, False, True),
        ("ORDER_DT", "주문일", "DATETIME", None, False, False, False),
        ("TOTAL_AMT", "총 주문 금액", "NUMBER", 14, False, False, False),
        ("SHIP_ADDR_ID", "배송 주소 ID", "NUMBER", 10, True, False, True),
        ("DELIVERY_STATUS", "배송 상태", "VARCHAR", 20, True, False, False),
    ]),
    ("PAYMENT_CARD", "결제 카드 정보", [
        ("PAY_ID", "결제 ID", "BIGINT", None, False, True, False),
        ("CARD_NO", "카드 번호", "VARCHAR", 16, False, False, False),
        ("CARD_EXP_DT", "카드 만료일", "DATE", None, False, False, False),
        ("BANK_ACCT_NO", "은행 계좌번호", "VARCHAR", 14, True, False, False),
        ("CUST_NO", "고객 번호", "NUMBER", 10, False, False, True),
        ("CC_HASH", "카드번호 해시", "VARCHAR", 64, False, False, False),
    ]),
    ("EMPLOYEE", "员工 목록", [
        ("EMP_NO", "사번", "NUMBER", 8, False, True, False),
        ("EMP_NAME", "직원 이름", "VARCHAR", 40, False, False, False),
        ("DEPT_CD", "부서 코드", "VARCHAR", 6, False, False, False),
        ("PHONE", "내선 전화", "VARCHAR", 10, True, False, False),
        ("HIRING_DT", "입사일", "DATE", None, False, False, False),
        ("SSN_MASK", "주민번호.mask", "VARCHAR", 13, False, False, False),
    ]),
    ("DEVICE_LOG", "디바이스 로그", [
        ("LOG_ID", "로그 ID", "BIGINT", None, False, True, False),
        ("IMEI", "IMEI", "VARCHAR", 15, False, False, False),
        ("IMSI", "IMSI", "VARCHAR", 15, True, False, False),
        ("IP_ADDR", "IP address", "VARCHAR", 40, False, False, False),
        ("LAT", "latitude", "DECIMAL", 9, True, False, False),
        ("LNG", "longitude", "DECIMAL", 9, True, False, False),
        ("EVENT_DT", " 이벤트 시간", "DATETIME", None, False, False, False),
        ("SKU_CODE", "상품 코드", "VARCHAR", 12, False, False, False),
    ]),
]

# Extra tables to reach the ~5,000 row operating scale.
BUSINESS_TABLES = [
    ("PRODUCT", None, [("PROD_ID", None, "BIGINT", None, False, True, False),
                       ("PROD_NAME", None, "VARCHAR", 80, False, False, False),
                       ("PRICE", None, "NUMBER", 12, False, False, False),
                       ("STOCK_QTY", None, "INT", None, False, False, False)]),
    ("SHIPMENT", None, [("SHIP_ID", None, "BIGINT", None, False, True, False),
                        ("TRACK_NO", None, "VARCHAR", 20, False, False, False),
                        ("WAREHOUSE_CD", None, "VARCHAR", 8, False, False, False),
                        ("DEPART_DT", None, "DATE", None, False, False, False)]),
    ("AUDIT_LOG", None, [("AUDIT_ID", None, "BIGINT", None, False, True, False),
                         ("USER_SYSTEM_ID", None, "VARCHAR", 20, False, False, False),
                         ("ACTION_CODE", None, "VARCHAR", 10, False, False, False),
                         ("CREATED_AT", None, "TIMESTAMP", None, False, False, False)]),
]


def generate_metadata(rows: int = 5000, path: Path | None = Path("tests/fixtures/metadata_5k.csv"),
                     headers: list[str] | None = None, seed: int = 7) -> Path:
    """Write an operating-scale DB metadata extract.

    The header names are deliberately varied (TBL_NAME instead of TABLE_NAME) so
    the field-mapping layer has to be exercised.
    """
    headers = headers or ["TBL_NAME", "TABLE_COMMENT", "COL_NAME", "COL_DESC", "DTYPE",
                          "LENGTH", "NULLABLE", "PK", "FK", "SCHEMA_NAME", "DB_NAME"]
    rng = random.Random(seed)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pool = list(TABLES) + list(BUSINESS_TABLES)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        written = 0
        index = 0
        while written < rows:
            table, comment, columns = pool[index % len(pool)]
            for column, col_comment, dtype, length, nullable, pk, fk in columns:
                if written >= rows:
                    break
                writer.writerow([
                    f"{table}_{index // len(pool) if index >= len(pool) else table}",
                    comment or "",
                    column,
                    col_comment or "",
                    dtype,
                    length if length is not None else "",
                    "Y" if nullable else "N",
                    "Y" if pk else "",
                    "Y" if fk else "",
                    "core" if index % 3 == 0 else "sales",
                    "prod_db",
                ])
                written += 1
                index += 1
    return path


def generate_small(path: Path = Path("tests/fixtures/metadata_small.csv")) -> Path:
    """A readable 12-row extract used by unit tests."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["TABLE_NAME", "COLUMN_NAME", "COLUMN_COMMENT", "DATA_TYPE",
                         "COLUMN_LENGTH", "PRIMARY_KEY", "FOREIGN_KEY"])
        writer.writerow(["CUSTOMER", "CUST_NO", "고객 번호", "NUMBER", 10, "Y", ""])
        writer.writerow(["CUSTOMER", "CUST_NAME", "고객 성명", "VARCHAR", 50, "", ""])
        writer.writerow(["CUSTOMER", "REG_NO", "주민등록번호", "VARCHAR", 13, "", ""])
        writer.writerow(["CUSTOMER", "POINT_AMT", "포인트", "NUMBER", 12, "", ""])
        writer.writerow(["ORDER_HEAD", "ORDER_ID", "주문 ID", "BIGINT", "", "Y", ""])
        writer.writerow(["ORDER_HEAD", "CUST_NO", "고객 번호", "NUMBER", 10, "", "Y"])
        writer.writerow(["PAYMENT_CARD", "CARD_NO", "카드 번호", "VARCHAR", 16, "", ""])
        writer.writerow(["PAYMENT_CARD", "CC_HASH", "카드번호 해시", "VARCHAR", 64, "", ""])
        writer.writerow(["DEVICE_LOG", "IMEI", "IMEI", "VARCHAR", 15, "", ""])
        writer.writerow(["DEVICE_LOG", "SKU_CODE", "상품 코드", "VARCHAR", 12, "", ""])
        writer.writerow(["EMPLOYEE", "EMP_NAME", "직원 이름", "VARCHAR", 40, "", ""])
        writer.writerow(["EMPLOYEE", "DEPT_CD", "부서 코드", "VARCHAR", 6, "", ""])
    return path


if __name__ == "__main__":
    print(generate_metadata())
    print(generate_small())
