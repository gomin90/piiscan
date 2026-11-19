"""지문/홍채 detector: CSV 문자열만으로는 확정 불가 -> 후보(candidate)로 분류."""
from __future__ import annotations

import re

from app.detectors.base import Detector, Match, ValueProfile

BIOMETRIC_RE = re.compile(r"(finger|print|지문|biometric|指纹)", re.IGNORECASE)
TEMPLATE_RE = re.compile(r"(template|minutiae|模板)", re.IGNORECASE)
MIME_RE = re.compile(r"image/(png|jpeg|jpg)|application/octet", re.IGNORECASE)
IMAGE_PREFIX_RE = re.compile(r"^(iVBOR|/9j/|R0lodGlm|SUkq|Zm9ybTp)")
MASKED_RE = re.compile(r"[*＊]{4,}")


class BiometricDetector(Detector):
    """Shared logic for fingerprint / iris image or template columns."""

    def match(self, value: str, profile: ValueProfile, column_name: str = "") -> Match | None:
        v = value.strip()
        if not v:
            return None
        if "blob" in profile.tags and profile.length >= 60:
            image_like = bool(IMAGE_PREFIX_RE.match(v))
            return Match(self.type_key, self.display_name, 0.85 if image_like else 0.6,
                         evidence=f"{self.display_name} Base64 blob (확정 불가, 후보)",
                         confirmed=False, priority=self.priority, sensitivity=self.sensitivity)
        if "hex" in profile.tags and profile.length >= 128:
            return Match(self.type_key, self.display_name, 0.6,
                         evidence=f"{self.display_name} hex 템플릿 (후보)",
                         confirmed=False, priority=self.priority, sensitivity=self.sensitivity)
        if MIME_RE.search(v) or TEMPLATE_RE.search(v) or BIOMETRIC_RE.search(v):
            return Match(self.type_key, self.display_name, 0.55,
                         evidence=f"{self.display_name} MIME/템플릿 표기 (후보)",
                         confirmed=False, priority=self.priority, sensitivity=self.sensitivity)
        if MASKED_RE.fullmatch(v) and profile.hangul == 0:
            return Match(self.type_key, self.display_name, 0.45, masked=True,
                         evidence="바이너리 마스킹 표시", priority=self.priority, sensitivity=self.sensitivity)
        return None


class FingerprintDetector(BiometricDetector):
    type_key = "FINGERPRINT"
    display_name = "지문정보"
    priority = 1
    sensitivity = 0.95
    candidate_tags = frozenset({"blob", "hex", "latin", "hangul", "masked"})
    name_hints = ("FINGER", "PRINT", "지문", "BIOMETRIC")


class IrisDetector(BiometricDetector):
    type_key = "IRIS"
    display_name = "홍채정보"
    priority = 1
    sensitivity = 0.95
    candidate_tags = frozenset({"blob", "hex", "latin", "hangul", "masked"})
    name_hints = ("IRIS", "홍채", "RETINA", "EYE")
