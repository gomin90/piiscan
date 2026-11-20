"""QA tests for the offline distribution bundle.

These checks run on the development machine and do not hash the multi-gigabyte
model binaries: they verify that the manifest, wheelhouse and Windows scripts are
mutually consistent.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from app.config.settings import project_root

ROOT = project_root()
MANIFEST = ROOT / "manifests" / "offline-bundle-manifest.json"
WHEEL_DIR = ROOT / "vendor" / "wheels"
LOCK = ROOT / "requirements" / "requirements-lock.txt"
WINDOWS_SCRIPTS = (
    "diagnose.ps1",
    "verify-offline-bundle.ps1",
    "install-offline.ps1",
    "build-exe.ps1",
    "build-jev-score.ps1",
    "fetch-cuda-backend.ps1",
    "run.bat",
)


def test_manifest_exists_and_is_well_formed() -> None:
    assert MANIFEST.is_file(), "manifests/offline-bundle-manifest.json is missing"
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["schema"] == "piiscan-offline-bundle/1.0"
    assert manifest["file_count"] == len(manifest["files"])
    for entry in manifest["files"]:
        assert {"path", "size", "sha256", "category"} <= set(entry)
        assert re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
        assert entry["size"] == (ROOT / entry["path"]).stat().st_size, f"{entry['path']} size changed since the manifest was generated"


def test_manifest_covers_windows_runtime_and_models() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    paths = {entry["path"] for entry in manifest["files"]}
    for required in (
        "runtime/windows/jev-score.exe",
        "runtime/windows/jev-score-v2.exe",
        "runtime/windows/llama.dll",
        "runtime/windows/ggml.dll",
        "runtime/windows/BUILD-INFO.json",
        "models/jev-style/0.8b-v3/manifest.json",
        "models/jev-style/2b-v4/manifest.json",
    ):
        assert required in paths, f"{required} is not in the bundle manifest"
    assert len([p for p in paths if p.startswith("vendor/wheels/")]) == len(list(WHEEL_DIR.glob("*.whl")))
    # the manifest cannot hash itself, so it is intentionally excluded
    assert "manifests/offline-bundle-manifest.json" not in paths


def test_lockfile_matches_wheelhouse() -> None:
    lock = LOCK.read_text(encoding="utf-8")
    locked = {line.split()[0] for line in lock.splitlines() if line.strip() and not line.startswith("#")}
    actual = {wheel.name for wheel in WHEEL_DIR.glob("*.whl")}
    assert locked == actual


def test_requirements_win_pins_are_in_the_wheelhouse() -> None:
    """Every pinned Windows requirement must have a matching wheel on disk."""
    reqs = [
        line.strip()
        for line in (ROOT / "requirements" / "requirements-win.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    wheels = {w.name.lower().replace("_", "-") for w in WHEEL_DIR.glob("*.whl")}
    for line in reqs:
        package, _, version = line.partition("==")
        stem = package.lower().replace("_", "-")
        assert any(w.startswith(stem + "-") for w in wheels), f"{package} is pinned but has no wheel"
        if version:
            assert any(w.startswith(stem + "-" + version + "-") for w in wheels), f"{line} is not the wheel shipped"


def test_windows_scripts_are_present() -> None:
    for name in WINDOWS_SCRIPTS:
        script = ROOT / "scripts" / "windows" / name
        assert script.is_file(), f"missing {script}"
        text = script.read_text(encoding="utf-8")
        assert len(text) > 200, f"{name} looks truncated"


def test_windows_scorer_uses_the_matching_protocol() -> None:
    build_info = json.loads((ROOT / "runtime" / "windows" / "BUILD-INFO.json").read_text(encoding="utf-8"))
    scorer = build_info["scorer"]
    assert scorer["jev-score.exe"]["protocol"] == "jev-score"
    assert scorer["jev-score-v2.exe"]["protocol"] == "jev-score-v2"
    assert scorer["jev-score.exe"]["model"] == "Jev-Style-0.8B-Decision-v3"
    assert scorer["jev-score-v2.exe"]["model"] == "Jev-Style-2B-Decision-v4"
    for entry in scorer.values():
        assert "not executed" in entry["execution_status"].lower()


def test_windows_sample_is_synthetic() -> None:
    """Every e-mail in the shipped sample uses an RFC 2606 reserved domain."""
    text = (ROOT / "samples" / "windows_sample.csv").read_text(encoding="utf-8")
    emails = re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+", text)
    assert emails, "the sample should contain e-mail columns"
    assert all(domain.split(".")[-1] in ("com", "org", "net", "example", "co.kr", "kr")
               and "example" in domain for domain in (e.split("@")[1] for e in emails)), emails


def test_python_version_coverage_of_the_wheelhouse() -> None:
    """install-offline.ps1 accepts CPython 3.12/3.13/3.14; the wheels it needs must exist."""
    wheels = {w.name for w in WHEEL_DIR.glob("*.whl")}
    for tag in ("cp312", "cp313", "cp314"):
        for package in ("numpy", "pyyaml"):
            assert any(w.startswith(package + "-") and tag in w for w in wheels), (
                f"no {package} wheel for {tag}; install-offline.ps1 would fail on Python 3.{tag[2:]}"
            )


def test_model_manifest_records_provenance() -> None:
    for release in ("0.8b-v3", "2b-v4"):
        release_dir = ROOT / "models" / "jev-style" / release
        manifest = json.loads((release_dir / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["repo"].startswith("chaoliangUNSW/Jev-Style-")
        quantized = next(name for name in manifest["files"] if name.endswith("Q4_K_M.gguf"))
        for name in (quantized, "tokenizer/tokenizer.json"):
            info = manifest["files"][name]
            assert re.fullmatch(r"[0-9a-f]{64}", info["sha256"])
            assert info["bytes"] == (release_dir / name).stat().st_size, f"{name} hash/size mismatch"
        package = json.loads((release_dir / "package_manifest.json").read_text(encoding="utf-8"))
        assert package["revision"]
        assert package["quantization"] == "Q4_K_M"
        assert package["license"], "licence must be recorded for every model"
        assert package["scorer"] in ("jev-score", "jev-score-v2")
