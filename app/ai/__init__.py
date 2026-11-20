"""AI layer: Jev-Style Decision Model (preferred) and the legacy local LLM engine."""
from __future__ import annotations

from app.ai.jev.engine import JevStyleEngine
from app.ai.jev.model_manager import JevStyleModelManager
from app.ai.llm_manager import LocalLLMEngine
from app.config.settings import AnalysisConfig


def create_ai_engine(config: AnalysisConfig, hardware) -> object:
    """AI 판정 엔진 선택.

    - "jev": Jev-Style Decision Model (State/Question/Options -> 선택지 확률)
    - "llm": 기존 generative local GGUF engine
    - "auto": try Jev-Style when a verified local model + scorer exists, else the LLM engine
    """
    engine_name = (config.ai_engine or "auto").lower()
    if engine_name in ("jev", "auto"):
        specs = JevStyleModelManager().discover()
        usable = [spec for spec in specs if spec.verified and spec.scorer_path is not None]
        if usable:
            return JevStyleEngine(config, hardware)
    return LocalLLMEngine(config, hardware)
