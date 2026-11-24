"""Jev-Style Decision Model 판정 엔진 (규칙 기반 엔진의 보조 layer).

LocalLLMEngine과 동일한 인터페이스(load/judge_column/memory_usage/summary/close)를
제공하여 analysis_service에서 interchangeably 사용된다. 모델이 없거나 로드 실패
하면 load()가 False를 반환하고 규칙 기반 결과만 사용된다.
"""
from __future__ import annotations

import time

from app.ai.gpu_detector import detect_hardware
from app.ai.jev.adapter import MARGIN_REVIEW, JevStyleAdapter
from app.ai.jev.decision import CODE_MEANING_OPTIONS, CODE_MEANING_QUESTION, JevStyleDecisionRequest
from app.ai.jev.model_manager import JevStyleModelManager
from app.config.settings import AnalysisConfig


class JevStyleEngine:
    def __init__(self, config: AnalysisConfig | None = None, hardware=None) -> None:
        self.config = config or AnalysisConfig()
        self.hardware = hardware or detect_hardware()
        self.manager = JevStyleModelManager()
        self.spec = None
        self.adapter: JevStyleAdapter | None = None
        self.status = "미로드"
        self.backend = "none"
        self.fallback_used = False
        self.load_seconds = 0.0
        self.inference_seconds = 0.0
        self.decisions = 0
        self.failures = 0
        self.peak_rss_mb = 0.0
        self.skipped_reason: str | None = None

    # --------------------------------------------------------------- lifecycle
    def load(self) -> bool:
        if not self.config.use_ai:
            self.status = "AI 비활성화 (use_ai=False) -> 규칙 기반 분석"
            self.skipped_reason = "use_ai=False"
            self.fallback_used = True
            return False

        available = self.manager.discover()
        if not available:
            self.status = ("Jev-Style 모델 없음 (models/jev-style/<release>/ 에 GGUF + scorer 필요) "
                          "-> 규칙 기반 분석")
            self.skipped_reason = "no model bundled"
            self.fallback_used = True
            return False

        self.spec = self.manager.select(self.hardware.available_ram_gb, self.config.jev_model)
        if self.spec is None:
            if any(spec.verified and spec.scorer_path for spec in available):
                self.status = (f"RAM 부족 (필요 {min(spec.rss_estimate_mb for spec in available)/1024:.1f}GB+, "
                               f"가능 {self.hardware.available_ram_gb:.1f}GB) -> 규칙 기반 분석")
                self.skipped_reason = "insufficient RAM for any Jev-Style model"
            else:
                self.status = f"선택 모델 {self.config.jev_model} 없음/검증 실패 -> 규칙 기반 분석"
                self.skipped_reason = f"model '{self.config.jev_model}' not usable"
            self.fallback_used = True
            return False
        if not self.spec.verified:
            self.status = f"모델 무결성 검증 실패 ({'; '.join(self.spec.problems)}) -> 규칙 기반 분석"
            self.skipped_reason = "sha256/integrity mismatch"
            self.fallback_used = True
            return False

        adapter = JevStyleAdapter(self.spec, self.hardware, threads=self.config.threads,
                                  gpu_layers=self.config.gpu_layers)
        if not adapter.load():
            self.status = adapter.status
            self.skipped_reason = adapter.last_error
            self.fallback_used = True
            return False

        self.adapter = adapter
        self.status = adapter.status
        self.backend = adapter.backend
        self.load_seconds = adapter.load_seconds
        return True

    @property
    def llm(self):
        return getattr(self.adapter, "engine", None)

    def memory_usage(self) -> dict:
        if self.adapter is None:
            return {"rss_mb": 0.0, "peak_rss_mb": 0.0}
        return self.adapter.memory_usage()

    def over_memory_limit(self) -> bool:
        return bool(self.adapter and self.adapter.over_memory_limit())

    # --------------------------------------------------------------- decisions
    def judge_column(self, column: dict) -> dict | None:
        if self.adapter is None:
            return None
        outcome = self.adapter.classify_column(column)
        self.decisions = self.adapter.decisions
        self.failures = self.adapter.failures
        self.inference_seconds = self.adapter.inference_seconds
        if outcome.get("jev_type") is None and outcome.get("personality") is None:
            return None
        return {
            "column": outcome["column"],
            "privacy_type": outcome["privacy_type"],
            "jev_type": outcome["jev_type"],
            "confidence": outcome["confidence"],
            "reason": outcome["reason"],
            "requires_review": outcome["requires_review"],
            "personality": outcome["personality"],
            "ai_model": self.spec.model_name if self.spec else "",
            "ai_backend": self.backend,
            "decisions": outcome["decisions"],
        }

    def classify_metadata(self, state: dict) -> dict:
        """Function B judgement: column meaning from metadata only, never values."""
        if self.adapter is None:
            return {"ok": False, "error": self.skipped_reason or "AI 미로드",
                    "verdict": None, "relevance": 0.0, "types": [], "decision": None,
                    "confidence": 0.0}
        outcome = self.adapter.classify_metadata(state)
        self.decisions = self.adapter.decisions
        self.failures = self.adapter.failures
        self.inference_seconds = self.adapter.inference_seconds
        return outcome

    def judge_code_group(self, group: dict) -> dict | None:
        if self.adapter is None:
            return None
        state = {
            "schema": group.get("schema", "CSV"),
            "table": "CSV",
            "column": group.get("group_column") or group.get("code_column") or "",
            "column_type": "TEXT",
            "sample_count": len(group.get("codes", [])),
            "value_patterns": [str(code)[:32] for code in (group.get("codes") or [])[:6]],
            "masked_samples": [],
        }
        request = JevStyleDecisionRequest(
            "column_meaning", state,
            CODE_MEANING_QUESTION, CODE_MEANING_OPTIONS, "choice", "general")
        result = self.adapter.decide_many([request])[0]
        if not result.ok:
            return None
        return {
            "group": group.get("group_column") or group.get("code_column"),
            "meaning": result.decision,
            "privacy_relevant": result.decision == "PERSONAL_DATA",
            "privacy_type": "CODE_GROUP" if result.decision == "PERSONAL_DATA" else "",
            "confidence": result.confidence,
            "requires_review": result.uncertain or result.margin < MARGIN_REVIEW,
        }

    def judge_row(self, record: dict) -> dict | None:
        """Row-context judgement for rows the rules could not settle."""
        if self.adapter is None:
            return None
        outcome = self.adapter.classify_row(record)
        self.decisions = self.adapter.decisions
        self.failures = self.adapter.failures
        self.inference_seconds = self.adapter.inference_seconds
        return outcome

    def judge_cells(self, column: dict, cells: list[dict]) -> list[dict]:
        """Batch Cell-level judgement: one scorer request per column batch.

        Returns one verdict per cell. Cells the model could not decide keep an
        explicit error so they are reported as 미평가 rather than 비개인정보.
        """
        if self.adapter is None:
            return []
        outcomes = self.adapter.classify_cells(column, cells)
        self.decisions = self.adapter.decisions
        self.failures = self.adapter.failures
        self.inference_seconds = self.adapter.inference_seconds
        return outcomes

    def close(self) -> None:
        if self.adapter is not None:
            # Keep the last adapter summary so the report still shows which backend
            # and how many layers actually ran on the GPU after the model is released.
            self.closing_summary = self.adapter.summary()
            self.peak_rss_mb = self.adapter.peak_rss_mb
            self.decisions = self.adapter.decisions
            self.failures = self.adapter.failures
            self.inference_seconds = self.adapter.inference_seconds
            self.adapter = None

    def summary(self) -> dict:
        if self.adapter is not None:
            data = self.adapter.summary()
        else:
            data = dict(getattr(self, "closing_summary", None) or {
                "model": self.spec.model_name if self.spec else None,
                "model_key": self.spec.key if self.spec else None,
                "quant": self.spec.quant if self.spec else None,
                "scorer": str(self.spec.scorer_path) if self.spec and self.spec.scorer_path else None,
                "status": self.status,
                "backend": self.backend,
                "load_seconds": round(self.load_seconds, 2),
                "inference_seconds": round(self.inference_seconds, 3),
                "decisions": self.decisions,
                "failures": self.failures,
                "peak_rss_mb": round(self.peak_rss_mb, 1),
                "context_limit": self.spec.context_limit if self.spec else 0,
                "scorer_info": {},
                "error": self.skipped_reason,
            })
        data["engine"] = "jev-style"
        data["skipped_reason"] = self.skipped_reason
        return data
