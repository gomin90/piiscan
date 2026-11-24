"""Function B: rule-based relevance scoring of a column's metadata.

Function B never sees stored values, so it cannot confirm personal data. It
reports *relevance* and whether the column must be checked against the real
table. The three verdicts are:

  개인정보 관련   strong structural/lexical evidence -> treat as a management target
  검토 필요       some evidence, not enough to decide
  관련 낮음       no evidence of personal data in the metadata

"관련 낮음" still says "확인 필요" in the report: metadata alone can never prove
the absence of personal data.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

RELEVANT = "개인정보 관련"
REVIEW = "검토 필요"
LOW = "관련 낮음"

# type_key -> (column name keywords, comment keywords, weight)
TYPE_SIGNALS: dict[str, tuple[tuple[str, ...], tuple[str, ...], float]] = {
    "RESIDENT_ID": (("resident", "ssn", "nationalid", "주민번호", "주민"),
                    ("주민", "national id"), 0.90),
    "PASSPORT": (("passport", "여권"), ("여권", "travel document"), 0.85),
    "DRIVER_LICENSE": (("driverlicense", "license", " 운전면허"), ("면허", "license"), 0.80),
    "FOREIGNER_ID": (("foreigner", "외국인"), ("외국인", "foreign"), 0.80),
    "SOCIAL_SECURITY": (("ssn", "socialsecurity"), ("social security", "사회보장"), 0.85),
    "INSURANCE_NUMBER": (("insurance", "policy", "보험"), ("보험", "policy no"), 0.65),
    "NAME": (("name", "fullname", "surname", "givenname", "nm", "person", "customer",
              "patient", "employee", "user", "contact", "이름", "성명", "고객", "환자",
              "직원", "사용자"),
             ("이름", "성명", "full name", "person name", "고객명", "환자명"), 0.80),
    "BIRTH_DATE": (("birth", "birthday", "dob", "생일", "출생"),
                   ("생년월일", "birthdate", "date of birth"), 0.85),
    "PHONE_NUMBER": (("phone", "tel", "mobile", "cell", "call", "연락", "전화", "휴대"),
                     ("phone", "tel", "연락처", "휴대폰", "전화번호"), 0.85),
    "EMAIL": (("email", "mail", "이메일", "메일"), ("email", "e-mail", "메일"), 0.85),
    "ADDRESS": (("address", "addr", "street", "zip", "postal", "주소", "우편"),
                ("address", "주소", "zip code", "postcode"), 0.75),
    "BANK_ACCOUNT": (("account", "bankacct", "계좌", "iban"),
                     ("account number", "계좌", "bank"), 0.80),
    "CARD_NUMBER": (("cardno", "cardnum", "card", "카드"),
                    ("card number", "카드", "credit card"), 0.75),
    "IMSI": (("imsi",), ("imsi",), 0.80),
    "IMEI": (("imei",), ("imei",), 0.80),
    "LOCATION": (("lat", "lng", "lon", "latitude", "longitude", "geo", "coords", "위도", "경도"),
                 ("coordinates", "latitude", "위치"), 0.60),
    "IP_ADDRESS": (("ipaddr", "ipaddress", "ip", "clientip"), ("ip address", "ip"), 0.55),
    "UUID": (("uuid", "guid"), ("uuid", "guid"), 0.45),
    "MAC_ADDRESS": (("macaddr", "macaddress", "mac"), ("mac address", "mac"), 0.55),
    "POSTCODE": (("postcode", "zipcode", "postal", "우편번호"), ("postal code", "zip"), 0.55),
    "GENDER": (("gender", "sex", "성별"), ("gender", "sex", "성별"), 0.50),
    "PROTECTION": (("hash", "encrypt", "mask", "digest", "sha", "md5", "암호", "해시"),
                   ("hashed", "encrypted", "masked", "보호된"), 0.50),
}

# A column name that plainly says "business/system data".
NON_PERSONAL_TOKENS = (
    "amount", "price", "total", "qty", "quantity", "count", "order", "invoice", "status",
    "flag", "type", "category", "level", "state", "created", "updated", "modified",
    "timestamp", "version", "score", "rate", "discount", "tax", "sku", "product",
    "shipment", "warehouse", "supplier", "dept", "department", "company", "organization",
    "url", "path", "file", "duration", "elapsed", "temperature", "weight", "size",
    "주문", "금액", " 총", "상태", "フラ", "회사", "부서", "제품", "배송", "창고",
)

# Generic identifier tokens: neither personal nor business code on their own.
IDENTIFIER_TOKENS = ("id", "no", "code", "seq", "key", "serial", "number", "num", "cd")

_NAME_RE = re.compile(r"[^0-9A-Za-zÀ-ÿ]+")


def _normalise(text: str) -> str:
    """Lower-case and drop separators, but keep Hangul: the signals are bilingual."""
    return re.sub(r"[^0-9A-Za-zÀ-ÿ\uAC00-\uD7A3]+", "", str(text or "").lower())


def _contains(haystack: str, needles: tuple[str, ...]) -> list[str]:
    """Return the needles present in `haystack` (CJK-safe substring check)."""
    return [needle for needle in needles if needle in haystack]


@dataclass
class ColumnMetadata:
    table: str
    column: str
    comment: str = ""
    data_type: str = ""
    length: int | None = None
    nullable: bool | None = None
    primary_key: bool = False
    foreign_key: bool = False
    schema: str = ""
    db: str = ""

    def as_dict(self) -> dict:
        return {
            "table": self.table, "column": self.column, "comment": self.comment,
            "data_type": self.data_type, "length": self.length, "nullable": self.nullable,
            "primary_key": self.primary_key, "foreign_key": self.foreign_key,
            "schema": self.schema, "db": self.db,
        }


@dataclass
class ColumnVerdict:
    table: str
    column: str
    verdict: str
    relevance: float
    type_candidates: list[str] = field(default_factory=list)
    rationale: list[str] = field(default_factory=list)
    method: str = "규칙"
    needs_confirmation: bool = True

    def as_dict(self) -> dict:
        return {
            "table_name": self.table, "column_name": self.column, "verdict": self.verdict,
            "relevance": round(self.relevance, 3), "type_candidates": self.type_candidates,
            "confidence": round(self.relevance, 3), "method": self.method,
            "rationale": "; ".join(self.rationale), "needs_confirmation": int(self.needs_confirmation),
        }


def score_metadata(column: ColumnMetadata) -> ColumnVerdict:
    """Score one column definition. Deterministic, no network, no values."""
    name = _normalise(column.column)
    comment = _normalise(column.comment)
    dtype = (column.data_type or "").upper()
    signals: list[str] = []
    candidates: list[str] = []
    score = 0.0

    for type_key, (name_tokens, comment_tokens, weight) in TYPE_SIGNALS.items():
        hits = _contains(name, name_tokens)
        if not hits and comment:
            hits = _contains(comment, comment_tokens + name_tokens)
        if hits:
            candidates.append(type_key)
            score += weight
            signals.append(f"{type_key} 시그널 ({hits[0]})")

    # A numeric identifier that merely *points at* a person (CUST_NO, USER_ID) is
    # not personal data by itself. Without a value, only the lexical hint exists,
    # so such a column drops to "검토 필요" instead of being a management target.
    # "USER_SYSTEM_ID", "CUST_STATUS_CD": a system identifier whose only personal
    # hint is the word "user"/"customer". The identifier itself is not personal
    # data; the linked record might be.
    identifier_like = name.endswith(IDENTIFIER_TOKENS) or (
        dtype in ("NUMBER", "INT", "INTEGER", "BIGINT", "SMALLINT")
        and any(token in name for token in IDENTIFIER_TOKENS))
    if candidates and set(candidates) == {"NAME"} and identifier_like:
        score -= 0.35
        signals.append(" Person-referencing identifier -> 값 확인 필요")

    if not candidates:
        if column.primary_key and column.length and column.length <= 24:
            score += 0.20
            signals.append(f"PRIMARY KEY ({column.length}) -> 식별자 가능성")
        if dtype in ("DATE", "DATETIME", "TIMESTAMP") and not any(
                token in name for token in ("created", "updated", "modified")):
            score += 0.15
            signals.append("날짜 타입 -> 개인 속성 가능성")
        if any(token in name for token in NON_PERSONAL_TOKENS):
            score -= 0.25
            signals.append("업무/시스템 데이터 시그널")

    # A short CHAR/VARCHAR with no lexical hint, or a BIGINT sequence, is usually
    # not personal data on its own - but that is a negative hint, never a proof.
    if not candidates and dtype in ("BIGINT", "INT", "INTEGER") and not column.length:
        score -= 0.10
        signals.append("정수 sequence -> 개인 데이터 가능성 낮음")

    relevance = max(0.0, min(1.0, score if score > 0 else 0.0))
    if relevance >= 0.60:
        verdict = RELEVANT
    elif relevance >= 0.25:
        verdict = REVIEW
    else:
        verdict = LOW
    if not signals:
        signals.append("메타데이터에서 개인정보 시그널 없음 (값 확인 필요)")

    return ColumnVerdict(table=column.table, column=column.column, verdict=verdict,
                         relevance=relevance, type_candidates=candidates,
                         rationale=signals, method="규칙")


def table_relevance(columns: list[ColumnVerdict]) -> dict:
    """Aggregate column verdicts into a table-level relevance."""
    if not columns:
        return {"relevance": 0.0, "pii_column_count": 0, "types": [], "status": "관련 낮음"}
    relevant = [c for c in columns if c.verdict == RELEVANT]
    review = [c for c in columns if c.verdict == REVIEW]
    types = sorted({t for c in columns for t in c.type_candidates})
    ratio = (len(relevant) + 0.5 * len(review)) / max(len(columns), 1)
    relevance = max((c.relevance for c in columns), default=0.0)
    status = RELEVANT if relevant else (REVIEW if review else LOW)
    return {
        "relevance": round(min(1.0, relevance + ratio * 0.3), 3),
        "pii_column_count": len(relevant),
        "review_column_count": len(review),
        "types": types,
        "status": status,
        "rationale": f" 관련 컬럼 {len(relevant)} / 검토 {len(review)} / 전체 {len(columns)}",
    }
