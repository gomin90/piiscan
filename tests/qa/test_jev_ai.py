"""QA: Jev-Style decision-model AI layer (fallback, privacy, integrity)."""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

from app.ai.jev.adapter import JevStyleAdapter, build_column_state, redact_sample, shape_pattern
from app.ai.jev.decision import (
    JEV_TYPE_TO_INTERNAL,
    PERSONALITY_OPTIONS,
    TYPE_OPTIONS,
    JevStyleDecisionRequest,
)
from app.ai.jev.engine import JevStyleEngine
from app.ai.jev.model_manager import JevStyleModelManager
from app.config.settings import AnalysisConfig
from app.core.analysis_service import analyse_file


class FakeEngine:
    """Deterministic stand-in for the official Jev-Style GGUF runtime."""

    def __init__(self, answer_map=None, fail=False):
        self.answer_map = answer_map or {}
        self.fail = fail
        self.states: list[dict] = []
        self.info = {"backend": "cpu", "model": "fake"}

    def _answer(self, options):
        names = list(options)
        return names[0] if names else "UNCERTAIN"

    def decide(self, state, question, options=None, qtype="choice", category=None):
        self.states.append({"state": state, "question": question, "options": options})
        if self.fail:
            raise RuntimeError("scorer crashed")
        options = options or {"true": None, "false": None}
        answer = self.answer_map.get(question, self._answer(options))
        n = len(options)
        probs = {name: (0.9 if name == answer else (0.1 / max(n - 1, 1))) for name in options}
        return {"answer": answer, "probabilities": probs, "top_probability": probs[answer],
                "input_tokens": 100, "backend": "cpu", "model": "fake"}

    def decide_many(self, state, questions, category=None):
        return [self.decide(state, q["ins"], q["crit"], q["t"], category) for q in questions]

    def close(self):
        pass


def test_redaction_never_keeps_full_digits():
    for raw in ["010-1234-5678", "900101-1234567", "john.doe@example.com",
                "4111 1111 1111 1111", "서울시 강남区区 121", "1990-01-01"]:
        redacted = redact_sample(raw)
        assert len(re.findall(r"\d", redacted)) <= 3, f"too many digits kept: {redacted} <- {raw}"
        assert raw not in redacted


def test_shape_pattern_is_reversible_free():
    assert shape_pattern("010-1234-5678") == "999-9999-9999"
    assert shape_pattern("abc") == "aaa"
    assert shape_pattern("ABC-12") == "AAA-99"
    assert "1234" not in shape_pattern("010-1234-5678")


def test_column_state_contains_no_raw_samples():
    state = build_column_state({
        "schema": "TB", "table": "MEMBER", "column": "PHONE_RAW",
        "stats": {"rows": 3, "non_empty": 3, "null": 0, "distinct_estimate": 3,
                  "samples": ["010-1234-5678", "9001011234567", "john@example.com"]},
    })
    rendered = json.dumps(state, ensure_ascii=False)
    for raw in ("010-1234-5678", "9001011234567", "john@example.com"):
        assert raw not in rendered
    assert state["value_patterns"], "state must carry value-shape patterns"
    assert state["charset"]["digit"] > 0.5


def test_adapter_without_model_is_inert():
    adapter = JevStyleAdapter(None, None)
    assert adapter.load() is False
    assert "모델 없음" in adapter.status
    result = adapter.decide(JevStyleDecisionRequest("type", {}, "q", TYPE_OPTIONS))
    assert result.error and not result.ok
    assert adapter.classify_column({"column": "A"})["privacy_type"] == "UNCERTAIN"
    adapter.close()


def test_adapter_reports_integrity_failure():
    manager = JevStyleModelManager()
    specs = manager.discover()
    if not specs:
        pytest.skip("Jev-Style 모델이 패키지에 없음 (offline build required)")
    from dataclasses import replace
    spec = replace(specs[0], verified=False, scorer_path=None)
    adapter = JevStyleAdapter(spec, None)
    assert adapter.load() is False
    assert "무결성" in adapter.status or "scorer" in adapter.status


def test_engine_decision_path_with_fake_engine():
    adapter = JevStyleAdapter(None, None)
    engine = FakeEngine()
    adapter.engine = engine
    adapter.backend = "cpu"
    column = {"schema": "TB", "table": "T", "column": "USER_NM",
              "stats": {"rows": 5, "non_empty": 5, "null": 0, "distinct_estimate": 5,
                       "samples": ["김길동", "이철수"]}}
    outcome = adapter.classify_column(column)
    assert outcome["privacy_type"] in JEV_TYPE_TO_INTERNAL.values()
    assert "USER_NM" in json.dumps(engine.states, ensure_ascii=False)
    for entry in engine.states:
        rendered = json.dumps(entry["state"], ensure_ascii=False)
        assert "김길동" not in rendered


def test_rule_results_survive_ai_failure():
    """AI crashes -> rule findings must still be present in the result rows."""
    engine = JevStyleEngine(AnalysisConfig(use_ai=True))
    engine.adapter = JevStyleAdapter(None, None)
    engine.adapter.engine = FakeEngine(fail=True)
    result = analyse_file(Path("tests/fixtures/synthetic/mixed_pii.csv"), llm=engine)
    assert result.results, "rule-based rows disappeared when AI failed"
    assert any(row.method == "규칙" or row.rule_confidence > 0 for row in result.results)
    assert result.ai_summary["engine"] == "jev-style"


def test_ai_disabled_keeps_rules():
    result = analyse_file(Path("tests/fixtures/synthetic/mixed_pii.csv"), AnalysisConfig(use_ai=False))
    assert result.results
    assert all(row.ai_confidence == 0.0 for row in result.results)


def test_ai_states_sent_to_model_contain_no_raw_pii(tmp_path: Path):
    """End-to-end: every State handed to the decision model is redacted."""
    from tests.qa.test_leakage import RAW_VALUES, build_fixture

    path = tmp_path / "jev_canary.csv"
    build_fixture(path)
    engine = JevStyleEngine(AnalysisConfig(use_ai=True))
    adapter = JevStyleAdapter(None, None)
    fake = FakeEngine()
    adapter.engine = fake
    adapter.backend = "cpu"
    adapter.status = "loaded"
    engine.adapter = adapter

    result = analyse_file(path, llm=engine)
    assert result.results, "rule rows must survive with the AI layer attached"
    payload = json.dumps(fake.states, ensure_ascii=False)
    for raw in RAW_VALUES:
        assert raw not in payload, f"raw personal value reached the AI state: {raw}"
    for state in fake.states:
        for sample in state["state"].get("masked_samples", []):
            assert any(mark in sample for mark in "*＊") or not sample, f"unmasked sample: {sample}"


def test_code_group_meaning_is_binary_and_measured():
    """코드 그룹 판정은 실측으로 선택한 3-choice binary option set를 사용한다."""
    from app.ai.jev.decision import CODE_MEANING_OPTIONS, CODE_MEANING_QUESTION

    assert set(CODE_MEANING_OPTIONS) == {"PERSONAL_DATA", "SYSTEM_OR_BUSINESS_CODE", "UNCERTAIN"}

    adapter = JevStyleAdapter(None, None)
    adapter.backend = "cpu"
    for answer, expected_relevant in (("PERSONAL_DATA", True), ("SYSTEM_OR_BUSINESS_CODE", False),
                                      ("UNCERTAIN", False)):
        adapter.engine = FakeEngine(answer_map={CODE_MEANING_QUESTION: answer})
        engine = JevStyleEngine(AnalysisConfig(use_ai=True))
        engine.adapter = adapter
        verdict = engine.judge_code_group({"group_column": "BIZ_CD", "code_column": "BIZ_CD",
                                          "codes": ["1100", "1200"]})
        assert verdict is not None
        assert verdict["meaning"] == answer
        assert verdict["privacy_relevant"] is expected_relevant
    adapter.close()


def test_ram_guard_selects_smaller_model():
    manager = JevStyleModelManager()
    specs = manager.discover()
    if len(specs) < 2:
        pytest.skip("both Jev-Style models must be bundled to test the RAM guard")
    big = max(specs, key=lambda spec: spec.size_bytes)
    small = min(specs, key=lambda spec: spec.size_bytes)
    assert manager.select(available_ram_gb=4.0).key == small.key
    assert manager.select(available_ram_gb=8.0).key == small.key
    assert manager.select(available_ram_gb=1.0) is None
    assert manager.select(available_ram_gb=8.0, prefer=big.key).key == big.key


def test_engine_skips_ai_when_ram_is_insufficient():
    manager = JevStyleModelManager()
    specs = manager.discover()
    if not specs:
        pytest.skip("no Jev-Style model bundled")
    from types import SimpleNamespace
    hardware = SimpleNamespace(available_ram_gb=1.0, backend="cpu", gpu_model="", vram_total_gb=0.0)
    engine = JevStyleEngine(AnalysisConfig(use_ai=True), hardware)
    assert engine.load() is False
    assert "RAM" in engine.status
    assert engine.summary()["skipped_reason"]
    result = analyse_file(Path("tests/fixtures/synthetic/mixed_pii.csv"), llm=engine)
    assert result.results, "rules must still produce findings when the model cannot load"


def _fake_gguf(path: Path) -> None:
    """Minimal GGUF header so integrity checks can run without a 500MB model."""
    kv = {"general.architecture": "qwen35", "general.name": "Jev-Style-0.8B-Decision-v3"}
    data = b"GGUF" + (3).to_bytes(4, "little") + (0).to_bytes(8, "little") + (len(kv)).to_bytes(8, "little")
    for key, value in kv.items():
        data += len(key).to_bytes(8, "little") + key.encode() + (8).to_bytes(4, "little")
        data += len(value).to_bytes(8, "little") + value.encode()
    path.write_bytes(data)


def _make_fake_bundle(root: Path, with_dlls: bool = True) -> Path:
    model_dir = root / "models" / "jev-style"
    release = model_dir / "0.8b-v3"
    release.mkdir(parents=True)
    gguf = release / "Jev-Style-0.8B-Decision-v3-Q4_K_M.gguf"
    _fake_gguf(gguf)
    digest = hashlib.sha256(gguf.read_bytes()).hexdigest()
    (release / "manifest.json").write_text(
        json.dumps({"files": {gguf.name: {"sha256": digest}}}), encoding="utf-8"
    )
    (release / "tokenizer").mkdir()
    (release / "tokenizer" / "tokenizer.json").write_text("{}", encoding="utf-8")
    (release / "readout_config.json").write_text("{}", encoding="utf-8")
    (release / "jev_style_decision_gguf.py").write_text("", encoding="utf-8")
    runtime = root / "runtime" / "windows"
    runtime.mkdir(parents=True)
    (runtime / "jev-score.exe").write_bytes(b"MZ fake scorer")
    (runtime / "jev-score-v2.exe").write_bytes(b"MZ fake scorer v2")
    if with_dlls:
        for dll in ("llama.dll", "ggml.dll", "ggml-base.dll", "libomp.dll", "ggml-cpu-x64.dll"):
            (runtime / dll).write_bytes(b"MZ fake dll")
    return model_dir


def test_windows_discovery_uses_exe_and_requires_llama_dlls(tmp_path, monkeypatch):
    from app.ai.jev import model_manager

    monkeypatch.setattr(model_manager, "IS_WINDOWS", True)
    monkeypatch.chdir(tmp_path)
    manager = JevStyleModelManager(root=_make_fake_bundle(tmp_path))
    spec = manager.discover()[0]
    assert spec.scorer_path.name == "jev-score.exe"
    assert spec.scorer_path.parent.name == "windows"
    assert spec.verified is True
    assert not spec.problems

    (tmp_path / "runtime" / "windows" / "ggml-base.dll").unlink()
    spec = manager.discover()[0]
    assert "ggml-base.dll" in " ".join(spec.problems)
    assert spec.verified is False


def test_windows_prefers_the_scorer_whose_runtime_dlls_are_present(tmp_path, monkeypatch):
    """install-cuda-backend.ps1 puts ggml-cuda.dll in runtime/windows, and llama.cpp only
    searches the executable's own directory, so the runtime scorer must win over the
    cross-built build/ exe that has no llama.cpp DLLs beside it."""
    from app.ai.jev import model_manager

    model_dir = _make_fake_bundle(tmp_path)
    build = model_dir / "0.8b-v3" / "build"
    build.mkdir(parents=True)
    (build / "jev-score.exe").write_bytes(b"MZ cross-built, no runtime next to it")

    monkeypatch.setattr(model_manager, "IS_WINDOWS", True)
    monkeypatch.chdir(tmp_path)
    spec = JevStyleModelManager(root=model_dir).discover()[0]
    assert spec.scorer_path.parent.name == "windows"
    assert spec.verified is True

    # Nothing runnable anywhere: the first existing file is still reported, with its problem.
    (tmp_path / "runtime" / "windows" / "llama.dll").unlink()
    spec = JevStyleModelManager(root=model_dir).discover()[0]
    assert "native library" in " ".join(spec.problems)
    assert spec.verified is False


def test_windows_finds_the_scorer_next_to_the_process_not_only_the_project_root(tmp_path, monkeypatch):
    """The bundle can be unpacked anywhere and run from that folder; the runtime sitting
    in the CWD (or next to a frozen EXE) must be searched too, otherwise the app reports
    'scorer not built' and the AI layer silently does nothing."""
    from app.ai.jev import model_manager

    bundle = tmp_path / "unpacked-bundle"
    model_dir = _make_fake_bundle(bundle)
    elsewhere = tmp_path / "run-from-here"
    runtime = elsewhere / "runtime" / "windows"
    runtime.mkdir(parents=True)
    (runtime / "jev-score.exe").write_bytes(b"MZ runtime scorer")
    for dll in ("llama.dll", "ggml.dll", "ggml-base.dll", "libomp.dll", "ggml-cpu-x64.dll"):
        (runtime / dll).write_bytes(b"x")
    # the unpacked bundle's own runtime copy is gone (e.g. only the model zip was extracted)
    for path in (bundle / "runtime" / "windows").iterdir():
        path.unlink()

    monkeypatch.setattr(model_manager, "IS_WINDOWS", True)
    monkeypatch.setattr(model_manager, "project_root", lambda: bundle)
    monkeypatch.chdir(elsewhere)
    spec = model_manager.JevStyleModelManager().discover()[0]
    assert spec.scorer_path == runtime / "jev-score.exe"
    assert spec.verified is True
    assert model_dir in spec.directory.parents


def test_windows_finds_a_natively_built_scorer(tmp_path, monkeypatch):
    """scripts/windows/build-jev-score.ps1 compiles the scorer into vendor/llama.cpp/build-native/bin."""
    from app.ai.jev import model_manager

    model_dir = _make_fake_bundle(tmp_path, with_dlls=False)
    for path in (tmp_path / "runtime" / "windows").iterdir():
        path.unlink()
    monkeypatch.chdir(tmp_path)
    native = tmp_path / "vendor" / "llama.cpp" / "build-native" / "bin"
    native.mkdir(parents=True)
    exe = native / "jev-score.exe"
    exe.write_bytes(b"MZ native scorer")
    for dll in ("llama.dll", "ggml.dll", "ggml-base.dll", "libomp.dll", "ggml-cpu-x64.dll"):
        (native / dll).write_bytes(b"x")

    monkeypatch.setattr(model_manager, "IS_WINDOWS", True)
    spec = JevStyleModelManager(root=model_dir).discover()[0]
    assert spec.scorer_path == exe
    assert spec.verified is True


def test_missing_scorer_names_the_places_it_searched(tmp_path, monkeypatch):
    from app.ai.jev import model_manager

    model_dir = _make_fake_bundle(tmp_path, with_dlls=False)
    for path in (tmp_path / "runtime" / "windows").iterdir():
        path.unlink()

    monkeypatch.setattr(model_manager, "IS_WINDOWS", True)
    monkeypatch.chdir(tmp_path)
    spec = JevStyleModelManager(root=model_dir).discover()[0]
    message = " ".join(spec.problems)
    assert "jev-score.exe" in message and "searched" in message


def test_model_and_scorer_are_found_next_to_the_process(tmp_path, monkeypatch):
    """A bundle unpacked anywhere and run from there must still expose its models and
    scorer, even though `app/config/settings.py` lives somewhere else."""
    from app.ai.jev import model_manager

    bundle = tmp_path / "unpacked-bundle"
    _make_fake_bundle(bundle)
    monkeypatch.setattr(model_manager, "IS_WINDOWS", True)
    monkeypatch.setattr(model_manager, "project_root", lambda: tmp_path / "does-not-exist")
    monkeypatch.chdir(bundle)
    monkeypatch.setattr(sys, "argv", [str(bundle / "dist" / "PrivacyScanner.exe")])

    manager = model_manager.JevStyleModelManager()
    assert manager.root == bundle / "models" / "jev-style"
    spec = manager.discover()[0]
    assert spec.scorer_path == bundle / "runtime" / "windows" / "jev-score.exe"


def test_mac_discovery_does_not_pick_windows_exe(tmp_path, monkeypatch):
    from app.ai.jev import model_manager

    monkeypatch.setattr(model_manager, "IS_WINDOWS", False)
    manager = JevStyleModelManager(root=_make_fake_bundle(tmp_path))
    spec = manager.discover()[0]
    assert spec.scorer_path is None
    assert "scorer binary" in " ".join(spec.problems)
    # macOS build dir is the only accepted location on POSIX
    (tmp_path / "models" / "jev-style" / "0.8b-v3" / "build").mkdir()
    (tmp_path / "models" / "jev-style" / "0.8b-v3" / "build" / "jev-score").write_bytes(b"binary")
    spec = manager.discover()[0]
    assert spec.scorer_path.name == "jev-score"


def test_piiscan_home_points_discovery_at_an_unpacked_bundle(tmp_path, monkeypatch):
    """PIISCAN_HOME is the documented escape hatch when the bundle is unpacked elsewhere."""
    from app.ai.jev import model_manager

    bundle = tmp_path / "somewhere-else"
    _make_fake_bundle(bundle)
    monkeypatch.setattr(model_manager, "IS_WINDOWS", True)
    monkeypatch.setenv("PIISCAN_HOME", str(bundle))
    monkeypatch.chdir(tmp_path)

    manager = model_manager.JevStyleModelManager()
    assert manager.root == bundle / "models" / "jev-style"
    spec = manager.discover()[0]
    assert spec.scorer_path == bundle / "runtime" / "windows" / "jev-score.exe"
    assert spec.verified is True
