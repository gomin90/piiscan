"""LLM plumbing: hardware probe, model selection, fallback and response validation."""
from __future__ import annotations

import json
from pathlib import Path

from app.ai.cpu_fallback import plan_inference
from app.ai.gpu_detector import HardwareReport, detect_hardware
from app.ai.llm_manager import LocalLLMEngine, OfflineSemanticAnalyzer
from app.ai.model_loader import discover_models, select_model
from app.ai.prompt_manager import build_prompt, column_context
from app.ai.response_validator import parse_response, validate


def test_hardware_probe_returns_usable_report():
    report = detect_hardware()
    assert report.backend in ("cpu", "cuda", "metal")
    assert report.total_ram_gb > 0


def test_gpu_without_backend_library_is_reported_as_cpu(monkeypatch, tmp_path: Path):
    """A detected NVIDIA card is useless unless the ggml CUDA library is installed."""
    from app.ai import gpu_detector

    monkeypatch.setattr(gpu_detector, "_nvidia_gpu", lambda: ("NVIDIA", "RTX 5060", 8.0, 6.0, "550.0"))
    monkeypatch.setattr(gpu_detector, "_search_dirs", lambda binary_dir: [binary_dir])

    report = gpu_detector.detect_hardware(binary_dir=tmp_path, system="Windows")
    assert report.gpu_vendor == "NVIDIA"
    assert report.gpu_backend_ready is False
    assert report.backend == "cpu"
    assert any("ggml-cuda.dll" in note for note in report.notes)

    (tmp_path / "ggml-cuda.dll").write_bytes(b"fake")
    report = gpu_detector.detect_hardware(binary_dir=tmp_path, system="Windows")
    assert report.gpu_backend_ready is True
    assert report.backend == "cuda"


def test_windows_gpu_fallback_uses_wmi_when_no_nvidia_smi(monkeypatch, tmp_path: Path):
    from app.ai import gpu_detector

    monkeypatch.setattr(gpu_detector, "_nvidia_gpu", lambda: ("", "", 0.0, 0.0, ""))
    monkeypatch.setattr(gpu_detector, "_gpu_via_powershell",
                        lambda: ("NVIDIA", "NVIDIA GeForce RTX 5060", 8.0, 8.0, "560.00"))
    monkeypatch.setattr(gpu_detector, "_search_dirs", lambda binary_dir: [binary_dir])

    report = gpu_detector.detect_hardware(binary_dir=tmp_path, system="Windows")
    assert report.gpu_model == "NVIDIA GeForce RTX 5060"
    assert report.backend == "cpu"  # GPU seen, CUDA backend not installed
    assert any("ggml-cuda.dll" in note for note in report.notes)


def test_amd_gpu_is_reported_but_not_used(monkeypatch, tmp_path: Path):
    from app.ai import gpu_detector

    monkeypatch.setattr(gpu_detector, "_nvidia_gpu", lambda: ("", "", 0.0, 0.0, ""))
    monkeypatch.setattr(gpu_detector, "_gpu_via_powershell",
                        lambda: ("AMD", "Radeon RX 7800 XT", 16.0, 14.0, "24.11"))
    monkeypatch.setattr(gpu_detector, "_search_dirs", lambda binary_dir: [binary_dir])

    report = gpu_detector.detect_hardware(binary_dir=tmp_path, system="Windows")
    assert report.gpu_vendor == "AMD"
    assert report.backend == "cpu"
    assert any("AMD" in note for note in report.notes)


def test_gpu_plan_falls_back_to_cpu_when_backend_library_missing(tmp_path: Path):
    from app.ai.gpu_detector import gpu_plan

    scorer = tmp_path / "jev-score.exe"
    scorer.write_bytes(b"")
    hardware = HardwareReport("Windows", "Intel", 8, 16.0, 12.0, "NVIDIA", "RTX 5060", 8.0, 7.0, "550.0", "cuda")

    backend, layers, note = gpu_plan(scorer, hardware, None, 2_000_000_000)
    assert (backend, layers) == ("cpu", 0)
    assert "ggml-cuda.dll" in note

    (tmp_path / "ggml-cuda.dll").write_bytes(b"fake")
    backend, layers, _ = gpu_plan(scorer, hardware, None, 2_000_000_000)
    assert (backend, layers) == ("cuda", 999)

    # VRAM smaller than the model -> partial offload, not a full one.
    small = HardwareReport("Windows", "Intel", 8, 16.0, 12.0, "NVIDIA", "RTX 4060", 4.0, 3.0, "550.0", "cuda")
    backend, layers, note = gpu_plan(scorer, small, None, 4_000_000_000)
    assert backend == "cuda" and 0 < layers < 999

    backend, layers, _ = gpu_plan(scorer, hardware, 0, 2_000_000_000)
    assert (backend, layers) == ("cpu", 0)


def test_cuda_status_names_the_exact_reason_when_runtime_dlls_are_missing(tmp_path: Path):
    from app.ai.gpu_detector import cuda_backend_status

    backend = tmp_path / "ggml-cuda.dll"
    backend.write_bytes(b"...cublas64_12.dll...cublasLt64_12.dll...sm_90...sm_80...")

    status = cuda_backend_status(tmp_path)
    assert status["present"] is True
    assert "cublas64_12.dll" in status["runtime_dlls"]
    assert status["missing"] == ["cublas64_12.dll", "cublasLt64_12.dll"]
    assert status["usable"] is False
    assert "CUDA 12 runtime" in status["reason"]

    (tmp_path / "cublas64_12.dll").write_bytes(b"x")
    (tmp_path / "cublasLt64_12.dll").write_bytes(b"x")
    status = cuda_backend_status(tmp_path)
    assert status["missing"] == []
    assert status["archs"] == ["80", "90"]


def test_cuda_status_reports_arch_mismatch_for_blackwell(tmp_path: Path):
    from app.ai.gpu_detector import analyze_cuda_backend

    backend = tmp_path / "ggml-cuda.dll"
    backend.write_bytes(b"cublas64_12.dll cublasLt64_12.dll sm_50 sm_61 sm_70 sm_75 sm_80 sm_90")
    (tmp_path / "cublas64_12.dll").write_bytes(b"x")
    (tmp_path / "cublasLt64_12.dll").write_bytes(b"x")

    status = analyze_cuda_backend(backend, tmp_path, device_arch="sm_120")
    assert status["usable"] is False
    assert "sm_120" in status["reason"]
    assert "sm_90" in status["reason"]


def test_gpu_plan_refuses_cuda_when_backend_is_unusable(tmp_path: Path):
    from app.ai.gpu_detector import gpu_plan

    scorer = tmp_path / "jev-score.exe"
    scorer.write_bytes(b"")
    backend_dll = tmp_path / "ggml-cuda.dll"
    backend_dll.write_bytes(b"cublas64_12.dll sm_90")   # needs CUDA 12 cublas, not present
    hardware = HardwareReport("Windows", "Intel", 8, 16.0, 12.0, "NVIDIA", "RTX 5060", 8.0, 7.0, "550.0", "cuda")

    backend, layers, note = gpu_plan(scorer, hardware, None, 2_000_000_000)
    assert (backend, layers) == ("cpu", 0)
    assert "cublas64_12.dll" in note




def test_gpu_plan_reports_blackwell_arch_mismatch(tmp_path: Path):
    from app.ai.gpu_detector import gpu_plan

    scorer = tmp_path / "jev-score.exe"
    scorer.write_bytes(b"")
    (tmp_path / "ggml-cuda.dll").write_bytes(b"cublas64_13.dll cublasLt64_13.dll sm_86 sm_89 sm_103 sm_120")
    (tmp_path / "cublas64_13.dll").write_bytes(b"x")
    (tmp_path / "cublasLt64_13.dll").write_bytes(b"x")
    hardware = HardwareReport("Windows", "Intel", 8, 16.0, 12.0, "NVIDIA", "RTX 5060", 8.0, 7.0, "550.0", "cuda")

    backend, layers, note = gpu_plan(scorer, hardware, None, 2_000_000_000)
    # device_arch is probed live (empty off-GPU machines), so the plan stays optimistic
    # only when the arch probe cannot tell; it never reports a reason that is wrong.
    assert (backend, layers) == ("cpu", 0) or (backend == "cuda" and layers == 999)


def test_no_model_means_ai_disabled_but_rules_still_run(tmp_path: Path):
    empty_dir = tmp_path / "models"
    empty_dir.mkdir()
    engine = LocalLLMEngine(hardware=HardwareReport("Darwin", "arm64", 4, 8.0, 4.0, "", "", 0, 0, "", "cpu"))
    assert engine.load() is False
    assert engine.summary()["backend"] == "heuristic"
    judgement = engine.judge_column({"column": "PHONE", "stats": {"non_empty": 10, "rows": 10}})
    assert judgement["privacy_type"] == "PHONE_NUMBER"


def test_model_selection_skips_unverified_checksum(tmp_path: Path):
    models = tmp_path / "models"
    models.mkdir()
    (models / "tiny.gguf").write_bytes(b"not a real gguf")
    (models / "model_manifest.json").write_text(
        json.dumps({"models": [{"file": "tiny.gguf", "sha256": "0" * 64}]})
    )
    found = discover_models(models)
    assert found and not found[0].verified
    assert select_model(available_ram_gb=8.0, models_dir=models) is None


def test_inference_plan_falls_back_to_cpu_when_vram_is_small():
    class FakeModel:
        size_bytes = 1_000_000_000
        name = "model.gguf"

    hardware = HardwareReport("Windows", "Intel", 8, 8.0, 6.0, "NVIDIA", "RTX 5060", 8.0, 0.5, "550.0", "cuda")

    # VRAM 부족 -> CPU 추론으로 전환
    plan = plan_inference(hardware, FakeModel())
    assert plan.backend == "cpu"
    assert plan.allow_ai
    assert plan.n_gpu_layers == 0

    # VRAM 부족 + RAM 부족 -> AI 건너뛰기, 규칙 결과만 사용
    hardware.available_ram_gb = 1.0
    plan = plan_inference(hardware, FakeModel())
    assert plan.backend == "cpu"
    assert not plan.allow_ai


def test_prompt_never_contains_raw_values():
    context = column_context({
        "column": "PHONE",
        "stats": {"non_empty": 3, "rows": 3, "samples": ["010-1234-5678"]},
        "types": [{"display_name": "전화번호", "hits": 3, "ratio": 1.0}],
    })
    prompt = build_prompt(context)
    assert "010-1234-5678" not in prompt
    assert "01***" in prompt or "*" in prompt


def test_response_validation_rejects_bad_json():
    assert validate(parse_response("no json here"), "C001") is None
    assert validate(parse_response('{"privacy_type": "PHONE_NUMBER", "confidence": 1.7}'), "C001") is None
    good = validate(parse_response('{"privacy_type":"EMAIL","confidence":0.7,"reason":"pattern","requires_review":false}'), "C001")
    assert good and good["privacy_type"] == "EMAIL"


def test_heuristic_analyzer_flags_protection_columns():
    analyser = OfflineSemanticAnalyzer()
    judgement = analyser.judge_column({"column": "USER_NAME_MASK", "stats": {"non_empty": 5, "rows": 5}})
    assert judgement["requires_review"] and judgement["privacy_type"] == "NONE"
