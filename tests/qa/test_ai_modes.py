"""QA: AI inference failure modes - no model, SHA256 mismatch, missing
llama.cpp, insufficient VRAM/RAM, CUDA init failure, memory guard. In every case the
rule based result must survive.
"""
from __future__ import annotations

import hashlib
import json
import sys
import types
from pathlib import Path

import pytest

from app.ai.cpu_fallback import plan_inference
from app.ai.gpu_detector import HardwareReport
from app.ai.llm_manager import LocalLLMEngine
from app.ai.model_loader import discover_models, select_model
from app.config.settings import AnalysisConfig
from app.core.analysis_service import analyse_file


def make_models_dir(tmp_path: Path, sha_ok: bool) -> Path:
    models = tmp_path / "models"
    models.mkdir(parents=True, exist_ok=True)
    payload = b"GGUF" + b"\x00" * 512
    path = models / "qwen2-1.5b-instruct-q4_k_m.gguf"
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    manifest = {"version": 1, "models": [{"file": path.name,
                                         "sha256": digest if sha_ok else "0" * 64,
                                         "size_bytes": len(payload), "context": 2048}]}
    (models / "model_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return models


def cpu_ram(ram: float) -> HardwareReport:
    return HardwareReport("Darwin", "Apple M-series", 8, ram + 2.0, ram, "", "", 0.0, 0.0, "", "cpu")


def test_missing_model_is_not_used():
    assert discover_models(Path("/nonexistent-models")) == []
    assert select_model(8.0, Path("/nonexistent-models")) is None


def test_bad_checksum_model_is_rejected(tmp_path: Path):
    models = make_models_dir(tmp_path, sha_ok=False)
    found = discover_models(models)
    assert len(found) == 1 and not found[0].verified
    assert select_model(8.0, models) is None


def test_verified_model_is_selected(tmp_path: Path):
    models = make_models_dir(tmp_path, sha_ok=True)
    model = select_model(8.0, models)
    assert model and model.verified


def test_model_larger_than_available_ram_is_skipped(tmp_path: Path):
    models = make_models_dir(tmp_path, sha_ok=True)
    big = models / "big-model.gguf"
    big.write_bytes(b"\x00" * 3_000_000)  # 3MB stub standing in for a large GGUF
    manifest = json.loads((models / "model_manifest.json").read_text())
    manifest["models"].append({"file": big.name,
                               "sha256": hashlib.sha256(big.read_bytes()).hexdigest(),
                               "size_bytes": big.stat().st_size, "context": 4096})
    (models / "model_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    # RAM 1.0GB cannot hold a 3MB model plus the 1.2GB runtime overhead.
    assert select_model(1.0, models) is None


@pytest.mark.parametrize("hw,expected_ai,expected_backend", [
    (HardwareReport("Windows", "i5", 4, 8.0, 6.0, "", "", 0.0, 0.0, "", "cpu"), True, "cpu"),
    (HardwareReport("Windows", "i5", 4, 2.0, 1.0, "", "", 0.0, 0.0, "", "cpu"), False, "cpu"),
    (HardwareReport("Windows", "i7", 8, 8.0, 6.0, "NVIDIA", "RTX 5060", 16.0, 15.0, "12.8", "cuda"), True, "cuda"),
    (HardwareReport("Windows", "i5", 4, 3.0, 2.0, "NVIDIA", "old GPU", 2.0, 1.0, "11.0", "cuda"), True, "cpu"),
    (HardwareReport("Darwin", "M3", 8, 8.0, 7.0, "", "", 0.0, 0.0, "", "metal"), True, "metal"),
])
def test_inference_plan(hw: HardwareReport, expected_ai: bool, expected_backend: str):
    from app.ai.model_loader import ModelInfo

    model = ModelInfo("m.gguf", "qwen2-1.5b-instruct-q4_k_m.gguf", 1_100_000_000, "x", True, 1.5)
    plan = plan_inference(hw, model, 2048)
    assert plan.allow_ai == expected_ai
    assert plan.backend == expected_backend
    assert plan.threads >= 1


def test_no_llama_cpp_still_produces_rule_results(tmp_path: Path):
    csv = tmp_path / "a.csv"
    csv.write_text("NAME,PHONE\n김민준,010-1234-5678\n", encoding="utf-8")
    engine = LocalLLMEngine(AnalysisConfig.load(), cpu_ram(8.0))
    assert engine.load() is False
    assert engine.backend == "heuristic"
    result = analyse_file(csv, llm=engine)
    assert result.results, "rule results must exist even with no AI"
    names = {r.display_name for r in result.results}
    assert {"전화번호", "이름"} <= names, names


def test_cuda_initialisation_failure_falls_back_to_cpu(tmp_path: Path):
    """llama.cpp raises on GPU init -> the CPU retry branch must run."""
    from app.ai.model_loader import ModelInfo

    class FailingLlama:
        calls = []

        def __init__(self, **kwargs):
            FailingLlama.calls.append(kwargs)
            if kwargs.get("n_gpu_layers", 0):
                raise RuntimeError("CUDA initialisation failed")

        def __call__(self, prompt, **kwargs):
            return {"text": "", "usage": {}}

    fake = types.ModuleType("llama_cpp")
    fake.Llama = FailingLlama
    previous = sys.modules.get("llama_cpp")
    sys.modules["llama_cpp"] = fake
    try:
        engine = LocalLLMEngine(AnalysisConfig.load(),
                                HardwareReport("Windows", "i7", 12, 16.0, 13.0,
                                              "NVIDIA", "RTX 5060", 16.0, 15.0, "12.8", "cuda"))
        hardware_model = ModelInfo("m.gguf", "qwen2-1.5b.gguf", 1_000_000, "x", True, 1.5)
        import app.ai.llm_manager as llm_module

        original_select = llm_module.select_model
        llm_module.select_model = lambda ram, models_dir=None: hardware_model
        try:
            loaded = engine.load()
        finally:
            llm_module.select_model = original_select

        csv = tmp_path / "b.csv"
        csv.write_text("RESIDENT_NO,NAME\n9001011234567,김민준\n", encoding="utf-8")
        result = analyse_file(csv, llm=engine)

        assert loaded, "CPU retry must succeed after the GPU init failure"
        assert engine.plan.backend == "cpu"
        assert engine.fallback_used
        assert result.results, "rule results preserved after AI fallback"
        assert FailingLlama.calls[0]["n_gpu_layers"] == -1
        assert FailingLlama.calls[1]["n_gpu_layers"] == 0
    finally:
        if previous is None:
            del sys.modules["llama_cpp"]
        else:
            sys.modules["llama_cpp"] = previous


def test_ram_guard_stops_ai_but_keeps_rules(tmp_path: Path):
    csv = tmp_path / "c.csv"
    csv.write_text("RESIDENT_NO,NAME,EMAIL\n9001011234567,김민준,a@b.co\n", encoding="utf-8")
    engine = LocalLLMEngine(AnalysisConfig.load(), cpu_ram(8.0))
    engine.llm = object()
    engine.memory_usage = lambda: {"rss_mb": 4200.0}
    engine.load = lambda: True
    result = analyse_file(csv, llm=engine)
    assert result.results, "memory guard must not discard rule based findings"
    assert engine.status == "메모리 정리 완료"


def test_model_integrity_mismatch_blocks_ai(tmp_path: Path):
    """model_manifest.json과 다른 GGUF이 들어있으면 AI 추론을 차단한다."""
    import app.ai.llm_manager as llm_module

    models = make_models_dir(tmp_path, sha_ok=False)
    monkey_patch = lambda: None
    original = llm_module.discover_models
    llm_module.discover_models = lambda models_dir=None: discover_models(models)
    try:
        engine = LocalLLMEngine(AnalysisConfig.load(), cpu_ram(8.0))
        assert engine.load() is False
        assert "무결성" in engine.status, engine.status
        assert engine.llm is None
    finally:
        llm_module.discover_models = original


def test_unwritable_temp_directory_blocks_model_load(tmp_path: Path):
    """build-guide 문서에서 주장하는 임시 directory 사용 가능 여부를 실제로 검사한다."""
    import tempfile

    import app.ai.llm_manager as llm_module
    from app.ai.model_loader import ModelInfo

    models = make_models_dir(tmp_path, sha_ok=True)
    model = select_model(8.0, models)
    original_select = llm_module.select_model
    original_tempdir = tempfile.gettempdir
    llm_module.select_model = lambda ram, models_dir=None: model
    tempfile.gettempdir = lambda: "/nonexistent-qa-dir"
    try:
        engine = LocalLLMEngine(AnalysisConfig.load(), cpu_ram(8.0))
        assert engine.load() is False
        assert "directory" in engine.status, engine.status
        assert engine.llm is None
    finally:
        llm_module.select_model = original_select
        tempfile.gettempdir = original_tempdir


def test_writable_temp_directory_passes_the_check(tmp_path: Path, monkeypatch):
    import app.ai.llm_manager as llm_module
    from app.ai.model_loader import ModelInfo

    models = make_models_dir(tmp_path, sha_ok=True)
    model = select_model(8.0, models)
    monkeypatch.setattr(llm_module, "select_model", lambda ram, models_dir=None: model)
    engine = LocalLLMEngine(AnalysisConfig.load(), cpu_ram(8.0))
    engine.load()
    # The only remaining blocker on this machine is the missing llama.cpp runtime.
    assert "directory" not in engine.status


def test_configured_threads_are_used_in_the_plan(tmp_path: Path, monkeypatch):
    """Settings의 threads 값이 추론 계획에 실제로 반영되는지 확인한다."""
    import app.ai.llm_manager as llm_module

    models = make_models_dir(tmp_path, sha_ok=True)
    model = select_model(8.0, models)
    monkeypatch.setattr(llm_module, "select_model", lambda ram, models_dir=None: model)
    engine = LocalLLMEngine(AnalysisConfig(threads=6), cpu_ram(16.0))
    engine.load()
    assert engine.plan.threads == 6
    # thread count never exceeds physical cores
    engine2 = LocalLLMEngine(AnalysisConfig(threads=99), HardwareReport("Darwin", "M3", 2, 16.0, 14.0, "", "", 0.0, 0.0, "", "cpu"))
    engine2.load()
    assert engine2.plan.threads == 2


def test_malformed_llm_json_is_retried_then_marked_unconfirmed(tmp_path: Path, monkeypatch):
    """문서의 '잘못된 JSON은 2번 재 시도 후 AI 미확정' behaviour를 실제로 확인한다."""
    import app.ai.llm_manager as llm_module
    from app.ai.model_loader import ModelInfo

    responses = ["not json at all",
                 '{"privacy_type": "PHONE_NUMBER", "confidence": 0.9, "reason": "column statistics"}']

    class FakeLlama:
        def __init__(self, **kwargs):
            self.i = 0

        def __call__(self, prompt, **kwargs):
            text = responses[self.i] if self.i < len(responses) else "garbage"
            self.i += 1
            return {"text": text, "usage": {}}

    fake = types.ModuleType("llama_cpp")
    fake.Llama = FakeLlama
    previous = sys.modules.get("llama_cpp")
    sys.modules["llama_cpp"] = fake

    models = make_models_dir(tmp_path, sha_ok=True)
    model = select_model(8.0, models)
    original_select = llm_module.select_model
    llm_module.select_model = lambda ram, models_dir=None: model
    try:
        engine = LocalLLMEngine(AnalysisConfig.load(), cpu_ram(8.0))
        assert engine.load() is True
        judgement = engine.judge_column({"column": "PHONE", "total_rows": 10, "privacy_hits": 8,
                                        "privacy_ratio": 0.8, "samples": ["010-****-5678"]})
        assert judgement and judgement["privacy_type"] == "PHONE_NUMBER"

        responses[:] = ["broken"] * 10
        engine.heuristic = type("H", (), {"judge_column": lambda self, c: None})
        assert engine.judge_column({"column": "PHONE", "total_rows": 10}) is None
    finally:
        llm_module.select_model = original_select
        if previous is None:
            del sys.modules["llama_cpp"]
        else:
            sys.modules["llama_cpp"] = previous
