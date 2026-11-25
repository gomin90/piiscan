"""Spec section 8: one scorer/backend resolution rule, used everywhere.

The failure mode these tests guard against is "a GPU backend exists somewhere on
the machine, so the app claims GPU inference" - the backend only counts when it
sits in a directory llama.cpp actually searches.
"""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from app.ai.scorer_resolver import (
    backend_search_dirs,
    find_backend,
    inference_report,
    scorer_candidates,
    select_scorer,
)


def test_env_variable_wins_over_the_model_build_directory():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        explicit = root / "custom" / "jev-score-v2"
        explicit.parent.mkdir(parents=True)
        explicit.write_text("binary")
        import os

        os.environ["JEV_SCORE_V2_BIN"] = str(explicit)
        try:
            chosen, notes = select_scorer(root / "models/jev-style/2b-v4", "jev-score-v2", root)
            assert chosen == explicit, notes
        finally:
            del os.environ["JEV_SCORE_V2_BIN"]


def test_candidate_order_is_explicit_env_then_build_then_runtime():
    root = Path("/nonexistent-project-root")
    candidates = scorer_candidates(Path("/models/jev-style/2b-v4"), "jev-score-v2", root)
    assert any("build" in str(c) for c in candidates)
    assert any("runtime" in str(c) for c in candidates)
    # a missing model directory simply yields no selection, never an exception
    chosen, notes = select_scorer(Path("/models/jev-style/2b-v4"), "jev-score-v2", root)
    assert chosen is None and notes


def test_backend_only_on_path_is_not_reported_as_found(monkeypatch, tmp_path: Path):
    on_path = tmp_path / "some-other-folder"
    on_path.mkdir()
    (on_path / "ggml-cuda.dll").write_text("library")
    monkeypatch.setenv("PATH", str(on_path))
    monkeypatch.delenv("GGML_BACKEND_DIR", raising=False)
    monkeypatch.delenv("GGML_BACKEND_PATH", raising=False)

    scorer = tmp_path / "jev-score-v2.exe"
    scorer.write_text("binary")
    info = find_backend(scorer, "Windows")
    assert info["found"] is False
    assert info["found_via_path"] is True
    assert "will not be loaded" in info["reason"]


def test_backend_next_to_scorer_is_found(monkeypatch, tmp_path: Path):
    (tmp_path / "ggml-cuda.dll").write_text("library")
    monkeypatch.delenv("PATH", raising=False)
    monkeypatch.delenv("GGML_BACKEND_DIR", raising=False)
    info = find_backend(tmp_path / "jev-score.exe", "Windows")
    assert info["found"] and Path(info["path"]).name == "ggml-cuda.dll"


def test_ggml_backend_dir_is_searched(monkeypatch, tmp_path: Path):
    backend_dir = tmp_path / "cuda-backend"
    backend_dir.mkdir()
    (backend_dir / "ggml-cuda.dll").write_text("library")
    monkeypatch.setenv("GGML_BACKEND_DIR", str(backend_dir))
    monkeypatch.delenv("PATH", raising=False)
    info = find_backend(tmp_path / "jev-score.exe", "Windows")
    assert info["found"] and info["source"] == str(backend_dir)


def test_report_never_calls_a_cpu_run_a_gpu_run(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("PATH", raising=False)
    report = inference_report(tmp_path / "jev-score.exe", "RTX 5060", "sm_120", 999, "cpu", "cpu")
    assert report["gpu_active"] is False
    assert report["requested_ngl"] == 999
    assert report["fallback_reason"]
    assert report["selected_scorer_path"].endswith("jev-score.exe")

    good = inference_report(tmp_path / "jev-score.exe", "RTX 5060", "sm_120", 999, 999, "cuda")
    assert good["gpu_active"] is True
    assert good["fallback_reason"] is None


def test_model_manager_and_gpu_detector_share_the_resolver():
    import inspect

    from app.ai import gpu_detector
    from app.ai.jev import model_manager

    assert "scorer_candidates" in inspect.getsource(model_manager.JevStyleModelManager)
    assert "find_backend" in inspect.getsource(gpu_detector.gpu_backend_present)
