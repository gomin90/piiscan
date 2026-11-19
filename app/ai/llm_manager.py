"""내장 Local LLM (llama.cpp) 추론 관리.

- GGUF 모델을 애플리케이션 내부에서 직접 로드한다 (localhost HTTP 서버 없음).
- GPU(CUDA/Metal) 가능하면 offload하고, otherwise CPU로 자동 전환한다.
- 모델/라이브러리 없을 deterministic 오프라인 의미 분석기로 fallback 한다.
- AI 결과는 규칙 결과를 덮어쓰지 않고 별도로 저장한다.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass

from app.ai.cpu_fallback import InferencePlan, plan_inference
from app.ai.gpu_detector import HardwareReport, detect_hardware
from app.ai.model_loader import ModelInfo, select_model
from app.ai.prompt_manager import build_code_group_prompt, build_prompt, column_context
from app.ai.response_validator import parse_response, validate
from app.config.settings import AnalysisConfig

NAME_TOKENS = {
    "NAME": "NAME", "이름": "NAME", "姓名": "NAME", "CUSTOMER": "NAME", "PATIENT": "NAME",
    "TEL": "PHONE_NUMBER", "PHONE": "PHONE_NUMBER", "MOBILE": "PHONE_NUMBER", "휴대폰": "PHONE_NUMBER",
    "EMAIL": "EMAIL", "MAIL": "EMAIL", "ADDR": "ADDRESS", "주소": "ADDRESS", "STREET": "ADDRESS",
    "RESIDENT": "RESIDENT_ID", "주민": "RESIDENT_ID", "PASSPORT": "PASSPORT", "여권": "PASSPORT",
    "DRIVER": "DRIVER_LICENSE", "면허": "DRIVER_LICENSE", "BIRTH": "BIRTH_DATE", "DOB": "BIRTH_DATE",
    "생년": "BIRTH_DATE", "CARD": "CARD_NUMBER", "카드": "CARD_NUMBER", "ACCOUNT": "BANK_ACCOUNT",
    "계좌": "BANK_ACCOUNT", "LAT": "LATITUDE", "LNG": "LONGITUDE", "LON": "LONGITUDE",
    "FINGER": "FINGERPRINT", "지문": "FINGERPRINT", "IRIS": "IRIS", "홍채": "IRIS",
    "IMSI": "IMSI", "IMEI": "IMEI", "UUID": "UUID", "MAC": "MAC", "IP": "IP",
    "POST": "POSTCODE", "ZIP": "POSTCODE", "우편": "POSTCODE", "SN": "SN", "SERIAL": "SN",
}

MASK_SUFFIXES = ("_MASK", "_MASKED", "_HASH", "_HASHED", "_ENC", "_ENCRYPT", "_CRYPT", "_SHA")


class OfflineSemanticAnalyzer:
    """Rule + meaning heuristics used when no GGUF model is available."""

    def judge_column(self, column: dict) -> dict | None:
        name = (column.get("column") or "").upper()
        stats = column.get("stats", {})
        if any(suffix in name for suffix in MASK_SUFFIXES):
            return {
                "column": column.get("column"),
                "privacy_type": "NONE",
                "confidence": 0.35,
                "reason": "보호(마스킹/해시/암호화) 컬럼으로 추정, 원천 컬럼 확인 필요",
                "requires_review": True,
            }
        for token, privacy_type in NAME_TOKENS.items():
            if token in name:
                return {
                    "column": column.get("column"),
                    "privacy_type": privacy_type,
                    "confidence": 0.45,
                    "reason": f"컬럼명 '{token}' 의미 기반 추정 (오프라인 의미 분석)",
                    "requires_review": True,
                }
        if stats.get("masked_like", 0) >= max(stats.get("non_empty", 0) * 0.5, 1):
            return {
                "column": column.get("column"),
                "privacy_type": "NONE",
                "confidence": 0.3,
                "reason": "값이 마스킹 형태로 보호됨",
                "requires_review": True,
            }
        return None

    def judge_code_group(self, group: dict) -> dict | None:
        return {
            "group": group.get("group_column") or group.get("code_column"),
            "meaning": "코드 그룹 (의미 규칙 미확정)",
            "privacy_relevant": bool(group.get("privacy_relevant")),
            "privacy_type": "CODE_GROUP",
            "confidence": 0.3,
            "requires_review": True,
        }


class LocalLLMEngine:
    def __init__(self, config: AnalysisConfig | None = None, hardware: HardwareReport | None = None):
        self.config = config or AnalysisConfig()
        self.hardware = hardware or detect_hardware()
        self.model: ModelInfo | None = None
        self.plan: InferencePlan | None = None
        self.llm = None
        self.backend = "none"
        self.status = "미로드"
        self.tokens_in = 0
        self.tokens_out = 0
        self.inference_seconds = 0.0
        self.load_seconds = 0.0
        self.fallback_used = False
        self.heuristic = OfflineSemanticAnalyzer()

    def load(self) -> bool:
        self.model = select_model(self.hardware.available_ram_gb)
        self.plan = plan_inference(self.hardware, self.model, self.config.context_length)
        if not self.config.use_ai or not self.plan.allow_ai or self.model is None:
            self.status = "AI 비활성화 (오프라인 의미 분석 사용)"
            self.backend = "heuristic"
            self.fallback_used = True
            return False
        try:
            from llama_cpp import Llama  # type: ignore
        except ImportError:
            self.status = "llama.cpp 라이브러리 없음 -> 오프라인 의미 분석 fallback"
            self.backend = "heuristic"
            self.fallback_used = True
            return False
        started = time.perf_counter()
        try:
            self.llm = Llama(
                model_path=self.model.path,
                n_ctx=self.plan.context_length,
                n_gpu_layers=self.plan.n_gpu_layers,
                n_threads=self.plan.threads,
                verbose=False,
            )
        except Exception:
            # GPU 초기화/추론 실패 -> CPU로 재시도, 실패 시 heuristic
            try:
                from llama_cpp import Llama  # type: ignore

                self.llm = Llama(model_path=self.model.path, n_ctx=1024, n_gpu_layers=0, n_threads=2, verbose=False)
                self.plan = InferencePlan("cpu", 0, 2, 1024, True, "GPU 초기화 실패 -> CPU fallback")
                self.fallback_used = True
            except Exception:
                self.status = "모델 로드 실패 -> 규칙 결과만 사용"
                self.backend = "none"
                self.fallback_used = True
                return False
        self.backend = self.plan.backend
        self.status = f"로드 완료 ({self.backend}, {self.model.name})"
        self.load_seconds = time.perf_counter() - started
        return True

    def _generate(self, prompt: str) -> str | None:
        if self.llm is None:
            return None
        try:
            response = self.llm(prompt, max_tokens=220, temperature=0.0)
        except Exception:
            return None
        text = response.get("text", "") if isinstance(response, dict) else ""
        usage = response.get("usage", {}) if isinstance(response, dict) else {}
        self.tokens_in += usage.get("prompt_tokens", 0)
        self.tokens_out += usage.get("completion_tokens", 0)
        return text

    def judge_column(self, column: dict) -> dict | None:
        if self.llm is None:
            return self.heuristic.judge_column(column)
        context = column_context({**column, "table": column.get("table", "")})
        prompt = build_prompt(context)
        started = time.perf_counter()
        for _ in range(2):
            text = self._generate(prompt)
            judgement = validate(parse_response(text), column.get("column", ""))
            if judgement:
                self.inference_seconds += time.perf_counter() - started
                return judgement
        self.inference_seconds += time.perf_counter() - started
        return None  # AI 미확정

    def judge_code_group(self, group: dict) -> dict | None:
        if self.llm is None:
            return self.heuristic.judge_code_group(group)
        started = time.perf_counter()
        text = self._generate(build_code_group_prompt(group))
        self.inference_seconds += time.perf_counter() - started
        data = parse_response(text)
        if not data:
            return None
        return {
            "group": data.get("group", ""),
            "meaning": str(data.get("meaning", ""))[:200],
            "privacy_relevant": bool(data.get("privacy_relevant", False)),
            "privacy_type": str(data.get("privacy_type", "CODE_GROUP")).upper(),
            "confidence": float(data.get("confidence", 0.0)),
            "requires_review": bool(data.get("requires_review", True)),
        }

    def memory_usage(self) -> dict:
        try:
            import psutil

            return {"rss_mb": round(psutil.virtual_memory().used / (1024 ** 2), 1)}
        except ImportError:
            return {"rss_mb": None}

    def close(self) -> None:
        if self.llm is not None:
            try:
                self.llm.close()
            except Exception:
                pass
            self.llm = None
            self.backend = "none"
            self.status = "메모리 정리 완료"

    def summary(self) -> dict:
        return {
            "backend": self.backend,
            "status": self.status,
            "model": self.model.name if self.model else None,
            "plan": self.plan.as_dict() if self.plan else None,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "inference_seconds": round(self.inference_seconds, 3),
            "load_seconds": round(self.load_seconds, 3),
            "fallback_used": self.fallback_used,
        }
