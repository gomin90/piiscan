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
