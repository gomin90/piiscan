"""Policy-driven detectors: 1/2순위 개인정보 유형 중 "값 형식"만으로 확정할
없는 유형들.

Section 1.4 of the specification separates three kinds of detectability:

* **format detectable** - a regex/checksum settles it (handled by the classic
  detectors in `app/detectors/*.py`),
* **context detectable only** - the column name, the table and the value shape
  together decide (this module),
* **not decidable from a CSV cell** - e.g. a face image itself; the value can
  only be reported as a *candidate*.

Every detector here is driven by `resources/rules/privacy_kr_2026.json`, so the
alias lists and priorities are editable without touching code. Matches from this
module are deliberately **not** hard-confirmed unless both the column meaning and
the value shape agree; otherwise they are reported as candidates (`검토 필요`)
and left to the AI/ human review. The generic "this looks like a number/hash/code"
short-cut in `cell_verdict.rule_negative_verdict` is skipped for these columns so
a `PASSWORD=123456` or `CELL_ID=ABC123456` cell is never silently cleared.
"""
from __future__ import annotations

import re

from app.config.privacy_policy import PrivacyPolicy, get_policy
from app.detectors.base import Detector, Match, ValueProfile

# ---------------------------------------------------------------------------
# Value shapes (shared by the context detectors)
# ---------------------------------------------------------------------------
ALNUM_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,63}$")
HEX_LIKE_RE = re.compile(r"^[0-9a-fA-F]{16,128}$")
B64_RE = re.compile(r"^[A-Za-z0-9+/]{24,}={0,2}$")
PHONE_LIKE_RE = re.compile(r"^(\+?\d{1,3}[-\s]?)?\d{2,4}[-\s]?\d{3,4}[-\s]?\d{4}$")
DATE_RE = re.compile(r"^(19|20)\d{2}[-/]\d{1,2}[-/]\d{1,2}$|^\d{6}$")
SMALL_COUNT_RE = re.compile(r"^\d{1,3}$")
CREDENTIAL_RE = re.compile(r"^[A-Za-z0-9!@#$%^&*_.\-]{4,64}$")
ATTRIBUTE_RE = re.compile(r"^[A-Za-z0-9+/#-]{1,12}$")
IMAGE_PREFIX_RE = re.compile(r"^(iVBOR|/9j/|R0lodGlm|SUkq|Zm9ybTp)")
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


#: A 32/64-character hex string is a digest, not a password a human typed.
#: Treating it as a confirmed credential would be a guess, so it stays a candidate.
HASH_SHAPE_RE = re.compile(r"^[0-9a-fA-F]{32,128}$")


def credential_shape(value: str, profile: ValueProfile | None = None) -> bool:
    text = (value or "").strip()
    if not text or text.lower() in ("null", "none", "n/a", "-"):
        return False
    return bool(CREDENTIAL_RE.fullmatch(text))


MASK_MARKER_RE = re.compile(r"[*＊]{2,}|[●■○]{2,}|\.{3,}")


PAYMENT_KEYWORD_RE = re.compile(r"(?:payment|pay|card|bill|invoice|trans(?:action)?|결제|카드|积分)",
                               re.IGNORECASE)
PAYMENT_DIGITS_RE = re.compile(r"\d{6,24}|(?:\d{3,4}-\d{4,10}(?:-\d{1,4})?)")
PAYMENT_RE = re.compile(r"^(?:[A-Za-z][A-Za-z0-9 _-]{2,20})?(?:\d{6,24}|\d{3,4}-\d{4,10}(?:-\d{1,4})?)$")


def payment_shape(value: str, profile: ValueProfile | None = None) -> bool:
    text = (value or "").strip()
    return bool(PAYMENT_KEYWORD_RE.search(text) and PAYMENT_RE.fullmatch(text))


def protected_shape(value: str, profile: ValueProfile | None = None) -> bool:
    text = (value or "").strip()
    return bool(MASK_MARKER_RE.search(text)) or bool(HEX_LIKE_RE.fullmatch(text)) \
        or bool(B64_RE.fullmatch(text))


def identifier_shape(value: str, profile: ValueProfile | None = None) -> bool:
    text = (value or "").strip()
    return bool(ALNUM_ID_RE.fullmatch(text)) or bool(HEX_LIKE_RE.fullmatch(text)) or bool(UUID_RE.fullmatch(text))


def weak_identifier(value: str, profile: ValueProfile | None = None) -> bool:
    """Anything that could be an identifier: keeps the cell unassessed rather than
    clearing it, without claiming a hit."""
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{3,32}", (value or "").strip()))


#: A cell-site / sector code is a run of 6-12 digits, optionally prefixed.
CELL_SITE_RE = re.compile(r"^(?:CELL|SECTOR|BS|ANT|REPCOD)[-_]?\d{4,12}$|^\d{6,12}$")
#: A network name carries a wireless keyword or is a 12+ character BSSID-style key.
SSID_KEYWORD_RE = re.compile(r"(?:wi[-.\s]?fi|wlan|ssid|network|ap[-_ ]| hotspot)", re.IGNORECASE)
SSID_BSSID_RE = re.compile(r"^[0-9A-Fa-f]{12,17}$|^[A-Za-z0-9 .:\-_]{10,64}$")
GUID_RE = re.compile(r"^(?:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
                     r"|[0-9a-fA-F]{16,32}|\d{16,32})$")


def cell_site_shape(value: str, profile: ValueProfile | None = None) -> bool:
    return bool(CELL_SITE_RE.fullmatch((value or "").strip().upper()))


def ssid_shape(value: str, profile: ValueProfile | None = None) -> bool:
    text = (value or "").strip()
    return bool(SSID_KEYWORD_RE.search(text) or SSID_BSSID_RE.fullmatch(text))


def guid_shape(value: str, profile: ValueProfile | None = None) -> bool:
    return bool(GUID_RE.fullmatch((value or "").strip()))


def gaid_shape(value: str, profile: ValueProfile | None = None) -> bool:
    text = (value or "").strip()
    return bool(GUID_RE.fullmatch(text)
               or re.fullmatch(r"(?:gaid|ad[-_]?id)[-_][0-9a-fA-F]{8,32}", text, re.IGNORECASE))


def binary_shape(value: str, profile: ValueProfile) -> bool:
    text = (value or "").strip()
    if not text:
        return False
    if "blob" in profile.tags or "hex" in profile.tags:
        return True
    return bool(IMAGE_PREFIX_RE.match(text)) or (len(text) >= 60 and text.count(",") >= 3)


class ContextDetector(Detector):
    """Template for types whose evidence is *column meaning + value shape*.

    `required_shape` is a predicate over the cell value. When the column name
    matches a policy alias but the value shape does not fit, the cell is not
    cleared either: it stays `미평가` (see `cell_verdict`), which keeps the
    "unassessed is not `non-personal`" invariant intact.
    """

    tier_from_policy = True
    #: Loose gate: a value that could plausibly be the type at all.
    required_shape = None
    #: Strict gate: a value that actually looks like the type. Only a strong shape
    #: plus column-name support may confirm a cell as personal data.
    strong_shape = None

    def __init__(self, policy: PrivacyPolicy | None = None):
        self.policy = policy or get_policy()
        definition = self.policy.get(self.type_key)
        if definition:
            self.display_name = definition.display_name_ko
            self.priority = definition.tier
            self.sensitivity = definition.sensitivity
            self.name_hints = definition.column_aliases
            self.negative_hints = tuple(definition.negative_hints)

    def column_affinity(self, column_name: str) -> float:
        return self.policy.column_affinity(self.type_key, column_name)

    def _candidate(self, column_name: str, value: str, shape_ok: bool,
                   extra: str = "") -> Match | None:
        """Context-only evidence.

        `shape_ok` means the *value* actually matches the type's strong shape. A
        column name alone is never enough to confirm a 2순위 identifier: the value
        must also look like the type, otherwise the cell stays a review candidate.
        """
        affinity = self.column_affinity(column_name)
        if affinity <= 0.0 or self.name_excludes(column_name):
            return None
        # Column meaning alone is weak evidence; a matching value shape is the
        # part that can push a cell over the confirm threshold.
        confidence = 0.40 + 0.25 * affinity + (0.30 if shape_ok else 0.0)
        confidence = min(confidence, 0.85)
        confirmed = shape_ok and affinity >= 0.40
        evidence = (f"컬럼 {column_name} -> {self.display_name} "
                    f"({'value shape 일치' if shape_ok else 'value shape 미확인'})")
        if extra:
            evidence += f" / {extra}"
        return Match(self.type_key, self.display_name, confidence,
                     evidence=evidence, confirmed=confirmed,
                     priority=self.priority, sensitivity=self.sensitivity,
                     tier=self.priority, legal_category=self.legal_category)

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        text = (value or "").strip()
        if not text:
            return None
        #: Column meaning is a prerequisite for every candidate here: without an
        #: alias match the detector can never produce a Match, so checking it first
        #: avoids running the value-shape regexes on unrelated columns.
        if self.column_affinity(column_name) <= 0.0 or self.name_excludes(column_name):
            return None
        if self.required_shape is not None and not self.required_shape(text, profile):
            return None
        strong = True if self.strong_shape is None else bool(self.strong_shape(text, profile))
        return self._candidate(column_name, text, strong,
                               extra="" if strong else "weak value shape -> 후보만")


class AuthCredentialDetector(ContextDetector):
    """PASSWORD / PASSWD / PIN / SECRET / TOKEN columns.

    A hash-like value in a credential column is still a candidate - the rules
    must never call it non-personal - but it is not *confirmed* as a credential,
    because the digest itself proves nothing about the underlying secret.
    """

    type_key = "AUTH_CREDENTIAL"
    display_name = "인증정보"
    priority = 1
    sensitivity = 0.9
    candidate_tags = frozenset({"digits", "alnum", "latin", "hex", "blob", "separated"})
    required_shape = staticmethod(credential_shape)

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        text = (value or "").strip()
        if not credential_shape(text):
            return None
        hash_like = bool(HASH_SHAPE_RE.fullmatch(text)) or B64_RE.fullmatch(text)
        return self._candidate(column_name, text, not hash_like,
                               extra="해시/보호 형태 -> 후보만" if hash_like else "")


class CustomerLinkageDetector(ContextDetector):
    """CI (연계정보): long opaque identifier in a customer column."""

    type_key = "CI"
    display_name = "연계정보(CI)"
    priority = 1
    sensitivity = 0.85
    candidate_tags = frozenset({"alnum", "hex", "digits", "blob"})
    required_shape = staticmethod(identifier_shape)

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        text = (value or "").strip()
        if len(text) < 16 or not identifier_shape(text):
            return None
        return self._candidate(column_name, text, True, extra=f"길이 {len(text)}")


class CellIdDetector(ContextDetector):
    """기지국/CELL ID: cell, sector, base-station identifiers."""

    type_key = "CELL_ID"
    display_name = "CELL ID/기지국"
    priority = 1
    sensitivity = 0.7
    candidate_tags = frozenset({"digits", "alnum", "latin", "dot_token", "separated"})
    required_shape = staticmethod(weak_identifier)
    strong_shape = staticmethod(cell_site_shape)


class RfidDetector(ContextDetector):
    """RFID/EPC tag identifiers."""

    type_key = "RFID"
    display_name = "RFID 태그 ID"
    priority = 1
    sensitivity = 0.7
    candidate_tags = frozenset({"digits", "alnum", "hex"})
    required_shape = staticmethod(lambda value, profile: bool(
        re.fullmatch(r"[0-9A-Za-z]{8,32}", (value or "").strip())))
    strong_shape = staticmethod(lambda value, profile: bool(
        re.fullmatch(r"(?:E280[0-9A-Fa-f]{12}|[0-9A-Fa-f]{16}|[0-9]{12,16})", (value or "").strip())))


class WirelessNetworkDetector(ContextDetector):
    """SSID / BSSID / AP / WLAN network identifiers."""

    type_key = "WIRELESS_NETWORK"
    display_name = "무선통신망 정보"
    priority = 1
    sensitivity = 0.6
    candidate_tags = frozenset({"alnum", "latin", "digits", "separated", "hex"})
    required_shape = staticmethod(lambda value, profile: bool(
        re.fullmatch(r"[A-Za-z0-9 .:\-_]{4,64}", (value or "").strip())))
    strong_shape = staticmethod(ssid_shape)


class FaxNumberDetector(ContextDetector):
    type_key = "FAX_NUMBER"
    display_name = "팩스번호"
    priority = 1
    sensitivity = 0.6
    candidate_tags = frozenset({"digits", "digits_sep", "alnum"})

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        text = (value or "").strip()
        if not PHONE_LIKE_RE.fullmatch(text):
            return None
        return self._candidate(column_name, text, True, extra="전화번호 형식")


class FaceBiometricDetector(ContextDetector):
    """Biometric/face template, image reference or embedding column."""

    type_key = "BIOMETRIC"
    display_name = "생체인식정보"
    priority = 1
    sensitivity = 1.0
    candidate_tags = frozenset({"blob", "hex", "latin", "long_text", "masked"})

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        text = (value or "").strip()
        if not text:
            return None
        shape_ok = binary_shape(text, profile) or bool(
            re.search(r"\.(png|jpg|jpeg|bmp|tif|tiff|webp)$", text, re.I))
        if not shape_ok:
            return None
        return self._candidate(column_name, text, True, extra="이미지/템플릿 형식")


class AccountGuidDetector(ContextDetector):
    """ACCOUNT GUID / GAID / PSID / Knox ID style customer identifiers (2순위)."""

    type_key = "ACCOUNT_GUID"
    display_name = "고객 식별 GUID"
    priority = 2
    sensitivity = 0.5
    candidate_tags = frozenset({"uuid", "alnum", "hex", "digits"})
    required_shape = staticmethod(identifier_shape)
    strong_shape = staticmethod(guid_shape)


class PersonalAttributeDetector(ContextDetector):
    """Marriage anniversary, number of children and similar identifiable attributes."""

    type_key = "PERSONAL_ATTRIBUTE"
    display_name = "고객 속성 정보"
    priority = 2
    sensitivity = 0.45
    candidate_tags = frozenset({"digits", "digits_sep", "latin"})

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        text = (value or "").strip()
        if not text or len(text) > 24:
            return None
        shape_ok = bool(DATE_RE.fullmatch(text) or SMALL_COUNT_RE.fullmatch(text)
                        or ATTRIBUTE_RE.fullmatch(text))
        if not shape_ok:
            return None
        return self._candidate(column_name, text, True, extra="속성 값 형식")

class MaskedDataDetector(ContextDetector):
    """HASH / MASK / ENC columns: the value is protected, never raw.

    A masked or hashed value must never be *cleared* as non-personal: the rules can
    not prove it is safe, and re-identification may still be possible. It therefore
    stays a candidate for review.
    """

    type_key = "MASKED"
    display_name = "보호 처리 컬럼"
    priority = 1
    sensitivity = 0.6
    candidate_tags = frozenset({"hex", "base64", "masked", "alnum", "digits"})
    required_shape = staticmethod(protected_shape)

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        text = (value or "").strip()
        if not text:
            return None
        if MASK_MARKER_RE.search(text):
            match = self._candidate(column_name, text, True, extra="마스킹 marker 감지")
            if match is not None:
                match.masked = True
            return match
        if HEX_LIKE_RE.fullmatch(text) or B64_RE.fullmatch(text):
            return self._candidate(column_name, text, True, extra="hash/encoding 형태")
        return None


class GaidDetector(ContextDetector):
    """GAID / advertising ID columns."""

    type_key = "GAID"
    display_name = "광고 식별 ID"
    priority = 2
    sensitivity = 0.5
    candidate_tags = frozenset({"uuid", "alnum", "hex", "digits"})
    required_shape = staticmethod(identifier_shape)
    strong_shape = staticmethod(gaid_shape)


class PaymentInfoDetector(ContextDetector):
    """Payment / loyalty references that may identify a person."""

    type_key = "PAYMENT_INFO"
    display_name = "결제 정보"
    priority = 2
    sensitivity = 0.6
    candidate_tags = frozenset({"digits", "alnum", "digits_sep", "latin"})
    required_shape = staticmethod(payment_shape)
    #: Confirmation needs a real transaction number (>=10 digits), not merely a
    #: payment-ish keyword next to a short reference.
    strong_shape = staticmethod(lambda value, profile: bool(
        re.search(r"\d{10,24}", (value or "").replace("-", ""))))

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        text = (value or "").strip()
        if not payment_shape(text):
            return None
        strong = bool(re.search(r"\d{10,24}", text.replace("-", "")))
        return self._candidate(column_name, text, strong,
                               extra="결제/멤버십 참조" if strong else "short reference -> 후보만")

