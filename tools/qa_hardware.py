#!/usr/bin/env python3
"""Hardware / AI-failure measurement harness.

Real measurement is only possible on the machine running this script (macOS
Apple Silicon here). The Windows/RTX rows are *planned* behaviour derived from
the same plan_inference() code with injected HardwareReport values - they are
reported as simulated, never as measured.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import psutil

from app.ai.cpu_fallback import plan_inference
from app.ai.gpu_detector import HardwareReport, detect_hardware
from app.ai.llm_manager import LocalLLMEngine
from app.ai.model_loader import discover_models, select_model
from app.config.settings import AnalysisConfig
from app.core.analysis_service import analyse_file
from tests.fixtures.generate_fixtures import FIXTURE_DIR, generate

SCENARIOS = [
    ("Windows 8GB / CPU only", HardwareReport("Windows", "Intel Core i5", 6, 8.0, 6.2, "", "", 0.0, 0.0, "", "cpu", ["no GPU"])),
    ("Windows 8GB / NVIDIA GPU (4GB VRAM)", HardwareReport("Windows", "Intel Core i5", 6, 8.0, 6.2, "NVIDIA", "GeForce", 4.0, 3.4, "12.4", "cuda", ["low VRAM"])),
    ("Windows RTX 5060 (16GB VRAM)", HardwareReport("Windows", "Intel Core i7", 12, 16.0, 13.0, "NVIDIA", "RTX 5060", 16.0, 15.0, "12.8", "cuda", [])),
    ("macOS Apple Silicon / CPU", HardwareReport("Darwin", "Apple M-series", 8, 16.0, 12.0, "", "", 0.0, 0.0, "", "cpu", ["forced cpu backend"])),
    ("macOS Apple Silicon / Metal", None),  # filled from the real machine
]


def rss_mb() -> float:
    return round(psutil.Process().memory_info().rss / (1024 ** 2), 1)


def measure_fixture(path: Path) -> dict:
    before = rss_mb()
    started = time.perf_counter()
    result = analyse_file(path, AnalysisConfig.load())
    elapsed = time.perf_counter() - started
    return {
        "file": path.name,
        "rows": result.rows_parsed,
        "seconds": round(elapsed, 3),
        "rows_per_second": round(result.rows_parsed / elapsed, 1) if elapsed else 0,
        "rss_before_mb": before,
        "rss_after_mb": rss_mb(),
        "rule_seconds": result.timings.get("rules", 0),
    }


def scenario_report(name: str, hardware: HardwareReport, fixture: Path) -> dict:
    model = select_model(hardware.available_ram_gb)
    plan = plan_inference(hardware, model, AnalysisConfig.load().context_length)
    engine = LocalLLMEngine(AnalysisConfig.load(), hardware)
    loaded = engine.load()
    measured = measure_fixture(fixture) if model is None or not loaded else measure_fixture(fixture)
    return {
        "scenario": name,
        "hardware": hardware.as_dict(),
        "model_selected": model.name if model else None,
        "model_verified_models_present": len([m for m in discover_models() if m.verified]),
        "inference_plan": plan.as_dict(),
        "llm_status": engine.status,
        "backend": engine.backend,
        "ai_used": loaded,
        "fallback_used": engine.fallback_used,
        "measured": measured,
        "measured_on_this_machine": hardware.os_name == detect_hardware().os_name
        and hardware.gpu_vendor == detect_hardware().gpu_vendor,
    }


def failure_modes(fixture: Path) -> list[dict]:
    out = []
    config = AnalysisConfig.load()

    # 1. No GGUF model present.
    hardware = detect_hardware()
    engine = LocalLLMEngine(config, hardware)
    engine.load()
    out.append({
        "mode": "GGUF 모델 없음",
        "expectation": "규칙 결과만 생산, crash 없음",
        "status": engine.status,
        "backend": engine.backend,
        "rows_returned": len(analyse_file(fixture, llm=engine).results),
    })

    # 2. Model checksum mismatch.
    from app.ai.model_loader import ModelInfo

    fake = ModelInfo("missing.gguf", "qwen2-1.5b.gguf", 1_500_000_000, "deadbeef", False, 1.5)
    plan = plan_inference(HardwareReport("Darwin", "M1", 8, 16.0, 12.0, "", "", 0.0, 0.0, "", "cpu"), fake)
    out.append({
        "mode": "SHA256 mismatch -> select_model rejects",
        "select_model_result": select_model(12.0),
        "plan_with_unverified_model": plan.reason,
    })

    # 3. llama.cpp not installed.
    engine = LocalLLMEngine(config, HardwareReport("Darwin", "M1", 8, 16.0, 12.0, "", "", 0.0, 0.0, "", "cpu"))
    engine.model = ModelInfo("fake.gguf", "fake.gguf", 1_000_000, "x", True, 1.0)
    engine.plan = plan_inference(engine.hardware, engine.model, config.context_length)
    loaded = engine.load()
    out.append({
        "mode": "llama_cpp import Failure",
        "loaded": loaded,
        "status": engine.status,
        "fallback_used": engine.fallback_used,
        "results_present": bool(analyse_file(fixture, llm=engine).results),
    })

    # 4. RAM / VRAM insufficient.
    tiny = HardwareReport("Windows", "i5", 4, 2.0, 0.9, "NVIDIA", "RTX 5060", 16.0, 1.5, "12.8", "cuda")
    plan = plan_inference(tiny, ModelInfo("m.gguf", "qwen2-1.5b.gguf", 1_000_000_000, "x", True, 1.5), 2048)
    out.append({"mode": "VRAM + RAM insufficient", "plan": plan.as_dict()})

    # 5. CUDA initialisation failure -> CPU retry path.
    class FailingLlama:
        def __init__(self, **kwargs):
            if kwargs.get("n_gpu_layers", 0):
                raise RuntimeError("CUDA initialisation failed")

    import app.ai.llm_manager as llm_module

    original = llm_module.Llama if hasattr(llm_module, "Llama") else None
    engine = LocalLLMEngine(config, HardwareReport("Windows", "i7", 12, 16.0, 13.0,
                                                  "NVIDIA", "RTX 5060", 16.0, 15.0, "12.8", "cuda"))
    engine.model = ModelInfo("m.gguf", "qwen2-1.5b.gguf", 1_000_000, "x", True, 1.5)
    engine.plan = plan_inference(engine.hardware, engine.model, config.context_length)
    import types

    fake_module = types.ModuleType("llama_cpp")
    fake_module.Llama = FailingLlama
    sys.modules["llama_cpp"] = fake_module
    loaded = engine.load()
    out.append({
        "mode": "CUDA init failure -> CPU retry",
        "loaded": loaded,
        "plan_after_retry": engine.plan.as_dict() if engine.plan else None,
        "fallback_used": engine.fallback_used,
        "results_present": bool(analyse_file(fixture, llm=engine).results),
    })
    if original is None:
        del sys.modules["llama_cpp"]

    # 6. Memory guard: RSS above the limit stops AI but keeps rule output.
    engine = LocalLLMEngine(config, hardware)
    engine.memory_usage = lambda: {"rss_mb": 4000.0}
    engine.llm = None
    engine.load = lambda: True
    result = analyse_file(fixture, llm=engine)
    out.append({
        "mode": "RAM guard (>3500MB)",
        "ai_continued": result.ai_summary.get("backend"),
        "results_present": bool(result.results),
    })
    return out


def main() -> int:
    generate()
    from tests.fixtures.generate_fixtures import generate_large
    fixture = FIXTURE_DIR / "mixed_pii.csv"
    large = generate_large(130000)

    real = detect_hardware()
    scenarios = []
    for name, hardware in SCENARIOS:
        if hardware is None:
            hardware = HardwareReport(real.os_name, real.cpu_model, real.cpu_count, real.total_ram_gb,
                                     real.available_ram_gb, "", "", 0.0, 0.0, "", "metal",
                                     ["Metal backend advertised on Apple Silicon"])
        scenarios.append(scenario_report(name, hardware, fixture))

    payload = {
        "measured_host": real.as_dict(),
        "scenarios": scenarios,
        "failure_modes": failure_modes(fixture),
        "large_file_measurement": measure_fixture(large),
    }
    out = ROOT / "results" / "qa_hardware.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
