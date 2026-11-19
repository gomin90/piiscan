"""GPU 실패 / VRAM 부족 시 CPU fallback 계획을 세운다."""
from __future__ import annotations

from dataclasses import dataclass

from app.ai.gpu_detector import HardwareReport
from app.ai.model_loader import ModelInfo


@dataclass
class InferencePlan:
    backend: str
    n_gpu_layers: int
    threads: int
    context_length: int
    allow_ai: bool
    reason: str

    def as_dict(self) -> dict:
        return {
            "backend": self.backend,
            "gpu_layers": self.n_gpu_layers,
            "threads": self.threads,
            "context": self.context_length,
            "ai_enabled": self.allow_ai,
            "reason": self.reason,
        }


def plan_inference(hardware: HardwareReport, model: ModelInfo | None, context_length: int = 2048,
                   threads: int | None = None) -> InferencePlan:
    if model is None:
        return InferencePlan("cpu", 0, 2, context_length, False,
                             "GGUF 모델 없음: 오프라인 모델 파일을 models/ 에 포함해야 함")

    model_gb = model.size_bytes / (1024 ** 3)
    # Respect the configured thread count, but never exceed the physical cores.
    thread_count = max(1, min(threads or 2, max(1, hardware.cpu_count)))

    if hardware.backend == "cuda":
        if hardware.vram_free_gb >= model_gb + 1.0:
            return InferencePlan("cuda", -1, thread_count, context_length, True,
                                 f"VRAM {hardware.vram_free_gb:.1f}GB -> full offload")
        if hardware.vram_free_gb >= 2.0:
            estimated_layers = max(1, int(hardware.vram_free_gb / max(model_gb, 0.5) * 12))
            return InferencePlan("cuda", estimated_layers, thread_count, min(context_length, 1024), True,
                                 f"VRAM {hardware.vram_free_gb:.1f}GB -> partial offload {estimated_layers} layers")
        if hardware.available_ram_gb >= model_gb + 0.8:
            return InferencePlan("cpu", 0, thread_count, min(context_length, 1024), True,
                                 "VRAM 부족 -> CPU 추론")
        return InferencePlan("cpu", 0, thread_count, min(context_length, 1024), False,
                             "VRAM 부족 + 시스템 RAM 부족 -> AI 추론 건너뛰기, 규칙 결과만 사용")

    available = hardware.available_ram_gb
    if available >= model_gb + 0.8:
        backend = "metal" if hardware.backend == "metal" else "cpu"
        return InferencePlan(backend, 0, thread_count, context_length, True, "RAM 충분 -> CPU/Metal 추론")
    return InferencePlan("cpu", 0, thread_count, min(context_length, 1024), False,
                         f"시스템 RAM {available:.1f}GB 부족 -> AI 추론 건너뛰기, 규칙 결과만 사용")
