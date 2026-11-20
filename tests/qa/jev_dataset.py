"""Jev-Style AI 판정용 독립 합성 데이터셋.

규칙 기반 QA 데이터셋(tests/qa/dataset.py)와 분리된다. 모든 값은 합성 데이터
이며 실제 개인정보를 포함하지 않는다. 각 예시는 컬럼 이름, 샘플 값,
expected_personality(개인정보 여부)와 expected_type(내부 유형 코드)을 담는다.
"""
from __future__ import annotations

RESIDENT_WEIGHTS = (2, 4, 6, 8, 1, 2, 4, 6, 8, 1, 2, 4)
LUHN_WEIGHTS = (2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4)

SURNAMES = ["김", "이", "박", "최", "정", "오", "윤", "홍", "장", "한"]
GIVEN = ["길동", "철수", "영희", "민준", "수지", "도준", "유나", "태준", "서연", "재호"]
CITIES = ["서울", "부산", "대구", "인천", "광주", "수원", "대전", "울산"]
ROADS = ["강남대로", "세종대로", "중앙로", "한강로", "국회대로", "판로"]
DEPTS = ["마케팅팀", "지원팀", "개발팀", "재무팀", "영업팀", "인사팀"]
PRODUCTS = ["노트프로 15", "무선 키보드", "결제 시스템", "데이터 분석 서비스", "모바일 앱 v2"]


def luhn(body: str) -> str:
    total = 0
    for index, ch in enumerate(reversed(body)):
        digit = int(ch)
        if index % 2 == 0:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return body + str((10 - total % 10) % 10)


def resident(birth: str, seq: str) -> str:
    body = birth + seq
    checksum = sum(weight * int(digit) for weight, digit in zip(RESIDENT_WEIGHTS, body))
    return body + str((10 - checksum % 10) % 10)


def examples() -> list[dict]:
    data: list[dict] = []

    def add(scenario: str, column: str, samples: list[str], expected_type: str,
            expected_personality: str, note: str = "") -> None:
        data.append({
            "scenario": scenario,
            "column": column,
            "samples": samples,
            "expected_type": expected_type,
            "expected_personality": expected_personality,
            "note": note,
        })

    # 1. 한국어 이름 (실제 이름 vs ID vs 회사/부서/상품)
    real_names = [f"{surname}{given}" for surname, given in zip(SURNAMES, GIVEN)]
    add("name_context", "USER_NAME", real_names, "NAME", "PERSONAL_DATA", "실제 사람 이름")
    add("name_context", "NM", real_names[:5], "NAME", "PERSONAL_DATA")
    add("name_context", "USER_ID", ["user_1024", "user_2048", "user_3001"], "CUSTOM_IDENTIFIER", "NON_PERSONAL_DATA", "사용자 ID")
    add("name_context", "COMPANY_NM", ["한국Telecom".replace("Telecom", "텔레콤"), "아래 주식회사", "미래 시스템"], "NONE", "NON_PERSONAL_DATA", "회사명")
    add("name_context", "DEPT_NM", DEPTS[:4] + DEPTS[4:], "NONE", "NON_PERSONAL_DATA", "부서명")
    add("name_context", "PRODUCT_NM", PRODUCTS, "NONE", "NON_PERSONAL_DATA", "상품명")
    add("name_context", "NICKNAME", ["star99", "cloud_x", "dev900"], "CUSTOM_IDENTIFIER", "PERSONAL_DATA", "닉네임(개인 식별)")
    add("name_context", "2", ["김", "박", "이"], "NAME", "PERSONAL_DATA", "컬럼명 없는 이름 컬럼")
    add("name_context", "CONTACT_PERSON", real_names[:4], "NAME", "PERSONAL_DATA")
    add("name_context", "AUTHOR_NM", ["김진", "이항", "박미"], "NAME", "PERSONAL_DATA")

    # 2. 주소
    add("address", "ADDR_ROAD", [f"{city}시 {road} 121" for city, road in zip(CITIES, ROADS)], "ADDRESS", "PERSONAL_DATA")
    add("address", "ADDR_DTL", ["501호", "3-402", "702호", "B-11"], "ADDRESS", "PERSONAL_DATA", "상세주소")
    add("address", "ADDR_LOT", [f"{city}시 {road} 45-3" for city, road in zip(CITIES, ROADS)], "ADDRESS", "PERSONAL_DATA", "지번주소")
    add("address", "CITY_NM", CITIES, "NONE", "NON_PERSONAL_DATA", "지역명만")
    add("address", "PROVINCE", ["경기도", "서울특별시", "부산광역시"], "NONE", "NON_PERSONAL_DATA", "행정구역")
    add("address", "ZIP", ["12345", "04512", "60201"], "OTHER_PERSONAL", "PERSONAL_DATA", "우편번호")
    add("address", "ADDR_TYPE", ["road", "lot", "road"], "NONE", "NON_PERSONAL_DATA", "주소 유형 코드")

    # 3. 생년월일
    add("type", "BIRTH_DT", ["19870101", "19920315", "19851220"], "BIRTH_DATE", "PERSONAL_DATA")
    add("type", "DOB", ["1990-05-12", "1988-11-03"], "BIRTH_DATE", "PERSONAL_DATA")
    add("type", "EVENT_DT", ["2024-01-05", "2023-06-30"], "NONE", "NON_PERSONAL_DATA", "이벤트 날짜")

    # 4. 전화번호
    add("type", "MOBILE", ["01012345678", "01098765432", "01055551234"], "PHONE_NUMBER", "PERSONAL_DATA")
    add("type", "PHONE", ["02-1234-5678", "051-333-2222"], "PHONE_NUMBER", "PERSONAL_DATA")
    add("type", "CALL_CTR", ["112", "119", "1300"], "NONE", "NON_PERSONAL_DATA", "공통번호")
    add("type", "PHONE_MASK", ["010-****-1234", "010-****-5678"], "PHONE_NUMBER", "PERSONAL_DATA", "불완전 마스킹")

    # 5. 주민등록번호
    add("type", "RESIDENT_NO", [resident("790101", "123456"), resident("850412", "234567")],
        "RESIDENT_ID", "SENSITIVE_PERSONAL_DATA")
    add("type", "PERS_NO", [resident("900101", "234567")], "RESIDENT_ID", "SENSITIVE_PERSONAL_DATA")
    add("type", "RESIDENT_BAD", ["790101-1234567".replace("-", ""), "1234567890123"], "NONE", "NON_PERSONAL_DATA", "체크섬 오류")

    # 6. 계좌번호
    add("type", "BANK_ACCT", [luhn("1234567890123"), luhn("9876543210123")], "BANK_ACCOUNT", "PERSONAL_DATA")
    add("type", "ACCT_CODE", ["1100", "1002", "2001"], "NONE", "NON_PERSONAL_DATA", "은행코드")

    # 7. 카드번호
    add("type", "CARD_NO", [luhn("411111111111111") + str(int(luhn("411111111111111")[-1])), "5555555555555555"],
        "CARD_NUMBER", "PERSONAL_DATA")
    add("type", "CARD_ID", ["C-1023", "C-1024"], "NONE", "NON_PERSONAL_DATA", "카드 ID")

    # 8. 외국인등록번호
    add("type", "FID_NO", ["A1234567890123", "F9876543210123"], "FOREIGNER_ID", "SENSITIVE_PERSONAL_DATA")

    # 9. 일반 텍스트
    add("type", "REMARK", ["테스트 메모", "문제 없음", "nachtrag"], "NONE", "NON_PERSONAL_DATA")
    add("type", "STATUS", ["Y", "N", "PENDING"], "NONE", "NON_PERSONAL_DATA")
    add("type", "DESCRIPTION", ["결제 처리 완료", "배송 대기"], "NONE", "NON_PERSONAL_DATA")

    # 10. 회사/부서 (재확인)
    add("type", "ORG_NM", ["서울중앙병원", "경기전자", "미래은행"], "NONE", "NON_PERSONAL_DATA")

    # 11. 공통 코드 / 업무 코드
    add("type", "CODE_GRP", ["01", "02", "03"], "NONE", "NON_PERSONAL_DATA", "공통코드")
    add("type", "BIZ_CD", ["1100", "1200", "2100"], "NONE", "NON_PERSONAL_DATA")
    add("type", "ORDER_NO", ["OD20240101001", "OD20231205002"], "CUSTOM_IDENTIFIER", "NON_PERSONAL_DATA", "주문번호")

    # 12. 보호 컬럼
    add("protection", "USER_NAME_MASK", ["김**", "이**", "박**"], "NAME", "PERSONAL_DATA", "마스킹")
    add("protection", "RESIDENT_HASH", ["9f86c083f1a4b2c7d6e5f4a3b2c1d0e9", "2c26b46b68ffc68ff99b453c1d304134"],
        "RESIDENT_ID", "PERSONAL_DATA", "해시")
    add("protection", "PHONE_ENC", ["U2VjcmV0ZWQgRGF0YQ==", "Zm9vYmFyYmF6ZXE="], "PHONE_NUMBER", "PERSONAL_DATA", "암호화")
    add("protection", "EMAIL_MASK", ["a***@***.com", "b***@***.net"], "EMAIL", "PERSONAL_DATA")
    add("protection", "ID_UNKNOWN", ["a1b2c3d4e5", "9f8e7d6c5b"], "NONE", "UNCERTAIN", "보호 여부 불명")

    # 13. 컬럼명과 값이 모순되는 경우
    add("type", "PHONE_NUMBER_COL", ["Y", "N", "Y"], "NONE", "NON_PERSONAL_DATA", "컬럼명 vs 값 모순")
    add("type", "EMAIL_ADDR", ["100000", "200000", "300000"], "NONE", "NON_PERSONAL_DATA", "숫자만")
    add("type", "NAME_COL", ["SYSTEM", "AUTO", "NULL"], "NONE", "NON_PERSONAL_DATA", "시스템 값")
    add("type", "CUST_DATA", ["12", "30", "45"], "NONE", "NON_PERSONAL_DATA", "숫자 코드")

    # 14. 불완전하게 마스킹된 개인정보
    add("type", "NAME_PART", ["홍*동", "김*수", "박*진"], "NAME", "PERSONAL_DATA")
    add("type", "RESIDENT_PART", ["790***01010", "850***12345"], "RESIDENT_ID", "PERSONAL_DATA")
    add("type", "CARD_PART", ["4111-****-****-1111", "5555-****-****-5555"], "CARD_NUMBER", "PERSONAL_DATA")
    add("type", "MOBILE_PART", ["010-12**-5678", "010-34**-9012"], "PHONE_NUMBER", "PERSONAL_DATA")

    # 15. 문맥에 따라 여부가 달라지는 컬럼
    add("type", "MEMBER_INFO", ["25", "40", "31"], "NONE", "UNCERTAIN", "나이일 수도, 코드일 수도")
    add("type", "CUST_DATA2", ["김길동", "이철수", "박영희"], "NAME", "PERSONAL_DATA", "이름 값")
    add("type", "SN", ["SN-10023", "SN-10024"], "CUSTOM_IDENTIFIER", "NON_PERSONAL_DATA", "시리얼")
    add("type", "IMEI", ["352789012345678", "351234567890123"], "IMEI", "PERSONAL_DATA")
    add("type", "IMSI", ["310260000000001", "310260000000002"], "IMSI", "PERSONAL_DATA")
    add("type", "LAT", ["37.5665", "33.4488", "35.1028"], "LOCATION", "PERSONAL_DATA")
    add("type", "LNG", ["126.9780", "132.6804", "128.7542"], "LOCATION", "PERSONAL_DATA")
    add("type", "UUID_COL", ["3f2a8b1c-4d5e-6f70-8192-a3b4c5d6e7f8", "9c8b7a6f-5e4d-3c2b-1a09-876543210abc"],
        "CUSTOM_IDENTIFIER", "NON_PERSONAL_DATA")
    add("type", "IP_COL", ["192.168.1.10", "10.0.0.55"], "OTHER_PERSONAL", "PERSONAL_DATA", "IP은 개인 장비 식별")
    return data
