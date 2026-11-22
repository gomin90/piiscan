"""Jev-Style Decision Model 판정 요청/결과 표준 형식."""
from __future__ import annotations

from dataclasses import dataclass, field

PERSONALITY_OPTIONS = {
    "PERSONAL_DATA": "특정 개인을 식별하거나 개인과 연결할 수 있는 데이터 (이름, 전화번호, 이메일, 집 주소)",
    "SENSITIVE_PERSONAL_DATA": "주민등록번호,카드번호, 계좌, 생체 정보 등 민감 개인정보",
    "NON_PERSONAL_DATA": "개인정보 아님: 코드, 회사명, 일반 텍스트",
    "UNCERTAIN": "판단불명",
}

TYPE_OPTIONS = {
    "RESIDENT_REGISTRATION_NUMBER": "주민등록번호: 13자리 숫자, YYYYMMDD + 6자리 + 1자리 check digit",
    "PASSPORT_NUMBER": "여권번호: 10자리 alphanumeric",
    "DRIVER_LICENSE": "운전면허번호: 8자리",
    "FOREIGNER_REGISTRATION_NUMBER": "외국인등록번호: 13자리 alphanumeric A~F로 시작",
    "NAME": "사람 이름: 2~4글 Hangul 또는 ascii 인명",
    "DATE_OF_BIRTH": "생년월일: 1900~2020년대 날짜",
    "PHONE_NUMBER": "전화번호: 10~11자리 숫자, 010/02 선",
    "EMAIL": "이메일: @ 포함",
    "ADDRESS": "주소: 도로명/지번/상세주소",
    "BANK_ACCOUNT": "은행 계좌번호: 10~14자리 숫자",
    "CARD_NUMBER": "카드번호: 16자리 숫자",
    "SOCIAL_SECURITY_NUMBER": "사회보장번호: 9자리 숫자",
    "INSURANCE_NUMBER": "보험번호",
    "BIOMETRIC_DATA": "지문, 홍채 등 생체 정보",
    "IMSI": "IMSI: 15자리 숫자",
    "IMEI": "IMEI: 15자리 숫자",
    "GEOLOCATION": "위도/경도 좌표",
    "CUSTOM_ID": "사용자 정의 ID: serial, UUID, 주문번호",
    "OTHER_PERSONAL_DATA": "기타 개인 데이터: 우편번호 5자리, IP 주소",
    "NON_PERSONAL_DATA": "개인정보가 아닌 업무 코드, 회사명, 일반 텍스트",
    "UNCERTAIN": "개인정보 유형 판단 불명",
}

JEV_TYPE_TO_INTERNAL = {
    "RESIDENT_REGISTRATION_NUMBER": "RESIDENT_ID",
    "PASSPORT_NUMBER": "PASSPORT",
    "DRIVER_LICENSE": "DRIVER_LICENSE",
    "FOREIGNER_REGISTRATION_NUMBER": "FOREIGNER_ID",
    "NAME": "NAME",
    "DATE_OF_BIRTH": "BIRTH_DATE",
    "PHONE_NUMBER": "PHONE_NUMBER",
    "EMAIL": "EMAIL",
    "ADDRESS": "ADDRESS",
    "BANK_ACCOUNT": "BANK_ACCOUNT",
    "CARD_NUMBER": "CARD_NUMBER",
    "SOCIAL_SECURITY_NUMBER": "SOCIAL_SECURITY",
    "INSURANCE_NUMBER": "INSURANCE_NUMBER",
    "BIOMETRIC_DATA": "BIOMETRIC",
    "IMSI": "IMSI",
    "IMEI": "IMEI",
    "GEOLOCATION": "LOCATION",
    "CUSTOM_ID": "CUSTOM_IDENTIFIER",
    "OTHER_PERSONAL_DATA": "OTHER_PERSONAL",
    "NON_PERSONAL_DATA": "NONE",
    "UNCERTAIN": "UNCERTAIN",
}

NAME_CONTEXT_OPTIONS = {
    "REAL_PERSON_NAME": "실제 사람 이름",
    "USER_ID": "사용자 ID / 로그인 이름",
    "COMPANY_NAME": "회사 또는 조직 명",
    "DEPARTMENT_NAME": "부서·팀 이름",
    "PRODUCT_NAME": "상품·서비스 이름",
    "GENERAL_TEXT": "일반 문자열 / 시스템 값",
    "UNCERTAIN": "구분할 수 없음",
}

ADDRESS_OPTIONS = {
    "ROAD_ADDRESS": "도로명주소",
    "LOT_ADDRESS": "지번주소",
    "DETAIL_ADDRESS": "상세주소 (층, 호수 등)",
    "REGION_NAME": "시·도·지역 명",
    "ADMIN_UNIT": "행정구역 명칭",
    "GENERAL_TEXT": "주소 아닌 일반 문자열",
    "UNCERTAIN": "구분할 수 없음",
}

PROTECTION_OPTIONS = {
    "SOURCE_PERSONAL_COLUMN": "보호되지 않은 원천 개인정보 컬럼",
    "MASKED_COLUMN": "마스킹된 컬럼",
    "HASHED_COLUMN": "해시된 컬럼",
    "ENCRYPTED_COLUMN": "암호화/인코딩된 컬럼",
    "PROTECTION_UNKNOWN": "보호 여부 불명",
    "UNCERTAIN": "판단 불가",
}

COLUMN_MEANING_OPTIONS = {
    "PERSONAL_IDENTIFIER": "개인을 식별하는 식별자",
    "PERSONAL_ATTRIBUTE": "개인 속성 (나이, 성별, 등급 등)",
    "CONTACT_CHANNEL": "연락 채널 (전화, 이메일)",
    "LOCATION_ATTRIBUTE": "위치 관련 속성",
    "FINANCIAL_ATTRIBUTE": "금융/결제 관련 속성",
    "SYSTEM_CODE": "시스템·업무 코드",
    "PROTECTION_DERIVATIVE": "보호 처리된 파생 컬럼",
    "NON_PERSONAL": "개인정보와 무관",
    "UNCERTAIN": "불명",
}

# 9-option 버전은 0.8B에서 0/9, 2B에서 3/9 Accuracy로 측정되어 코드 그룹 판정에는
# 3-choice binary를 사용한다 (실측: 0.8B 0.30, 2B 0.90).
ROW_PERSONALITY_QUESTION = "이 행(Row)은 특정 개인을 식별할 수 있는 개인정보를 포함하는가?"

CODE_MEANING_QUESTION = "이 컬럼은 개인 관련 데이터인가, 아니면 시스템/업무 코드나 일반 값인가?"
CODE_MEANING_OPTIONS = {
    "PERSONAL_DATA": "개인 관련 데이터: 개인을 식별·연락·위치·속성·금융 정보 또는 보호 처리된 파생 컬럼",
    "SYSTEM_OR_BUSINESS_CODE": "시스템/업무 코드 또는 일반 값: 개인과 무관",
    "UNCERTAIN": "불명",
}


@dataclass
class JevStyleDecisionRequest:
    scenario: str
    state: dict
    question: str
    options: dict[str, str] | None = None
    qtype: str = "choice"
    category: str | None = None

    def as_dict(self) -> dict:
        return {"scenario": self.scenario, "state": self.state, "question": self.question,
                "options": self.options, "qtype": self.qtype, "category": self.category}


@dataclass
class JevStyleDecisionResult:
    scenario: str
    decision: str | None
    probabilities: dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0
    margin: float = 0.0
    input_tokens: int = 0
    latency_ms: float = 0.0
    model: str = ""
    backend: str = ""
    error: str | None = None
    raw: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None and self.decision is not None

    @property
    def uncertain(self) -> bool:
        return self.decision in (None, "UNCERTAIN")

    def as_dict(self) -> dict:
        return {
            "scenario": self.scenario,
            "decision": self.decision,
            "probabilities": {key: round(value, 4) for key, value in self.probabilities.items()},
            "confidence": round(self.confidence, 3),
            "margin": round(self.margin, 3),
            "input_tokens": self.input_tokens,
            "latency_ms": round(self.latency_ms, 1),
            "model": self.model,
            "backend": self.backend,
            "error": self.error,
        }
