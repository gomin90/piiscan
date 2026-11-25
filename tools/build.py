"""Platform build wrapper.

    python tools/build.py --target macos
    python tools/build.py --target windows --debug

Builds a single-file executable with PyInstaller and bundles the resources and
native libraries. Model integrity is verified with SHA256.

GGUF weights are NOT embedded in the `--onefile` executable: a 0.8 GB / 1.3 GB
weight file inside a single-file EXE makes start-up expand the whole archive and
makes the base package far bigger than the code it ships. By default the base
build carries code + scorer + tokenizer only, and the weights ship as the
se optional model packages produced by `tools/make_release_bundle.py --split`
(piiscan-app.zip / piiscan-model-0.8b.zip / piiscan-model-2b.zip). Pass
`--embed-models` only when a truly single-file artifact is required.
"""
from __future__ import annotations

import sys


import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
ENTRY = ROOT / "app" / "main.py"
MANIFEST = ROOT / "models" / "model_manifest.json"


def verify_jev_models() -> bool:
    """Jev-Style 오프라인 bundle: GGUF + scorer + tokenizer 무결성 확인."""
    from app.ai.jev.model_manager import JevStyleModelManager

    specs = JevStyleModelManager().discover()
    if not specs:
        print("[warn] no Jev-Style models in models/jev-style (rule-only bundle)")
        return True
    ok = True
    for spec in specs:
        if spec.verified and spec.scorer_path:
            print(f"[ok] {spec.model_name} {spec.quant} sha256={spec.sha256[:16]} scorer={spec.scorer_path.name}")
        else:
            print(f"[error] {spec.model_name}: {spec.problems}")
            ok = False
    return ok


def verify_models() -> bool:
    if not MANIFEST.exists():
        print("[warn] models/model_manifest.json not found")
        return True
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    ok = True
    for entry in manifest.get("models", []):
        path = ROOT / "models" / entry["file"]
        if not path.exists():
            print(f"[warn] model missing: {path} (offline bundle must include it)")
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if entry.get("sha256") and entry["sha256"] != digest:
            print(f"[error] checksum mismatch for {entry['file']}: {digest}")
            ok = False
        else:
            print(f"[ok] {entry['file']} sha256 verified")
    return ok


def check_environment() -> bool:
    missing = []
    for module in ("PySide6", "PyInstaller"):
        try:
            __import__(module)
        except ImportError:
            missing.append(module)
    if missing:
        print(f"[error] missing packages: {', '.join(missing)}")
        print("Install offline wheels first (see docs/offline-build.md).")
        return False
    try:
        import llama_cpp  # noqa: F401
    except ImportError:
        print("[warn] llama-cpp-python not installed: AI inference will fall back to the offline analyser")
    return True


def run_tests() -> bool:
    result = subprocess.run([sys.executable, "-m", "pytest", "tests", "-q"], cwd=ROOT)
    return result.returncode == 0


def staged_models_root() -> Path:
    """Copy the model tree without the GGUF weights, for `--add-data`.

    Scorer binaries, tokenizers and readout configs stay: the offline AI layer
    needs them, and they are small. Only the multi-hundred-MB weights are left
    out so the base executable stays a base executable.
    """
    stage = ROOT / "dist" / "models-stage"
    models = ROOT / "models"
    if not models.exists():
        return stage
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True, exist_ok=True)
    skipped: list[str] = []
    for path in models.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() == ".gguf":
            skipped.append(str(path.relative_to(ROOT)))
            continue
        dest = stage / path.relative_to(models)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
    if skipped:
        print(f"[packaging] GGUF weights not embedded in the onefile EXE "
              f"({len(skipped)} files, ship them as separate model packages): "
              + ", ".join(skipped))
    return stage


def build(target: str, debug: bool, skip_tests: bool = False, rule_only: bool = False,
          embed_models: bool = False) -> int:
    if not rule_only and (not verify_models() or not verify_jev_models()):
        return 1
    if not check_environment():
        return 1
    if not skip_tests and not run_tests():
        return 1

    from PyInstaller.__main__ import run as pyinstaller_run

    args = [
        str(ENTRY),
        "--onefile",
        "--name=PrivacyScanner",
        "--noconfirm",
        f"--add-data={ROOT / 'resources'}{pathsep()}resources",
    ]
    if not rule_only:
        if embed_models:
            args.append(f"--add-data={ROOT / 'models'}{pathsep()}models")
        else:
            args.append(f"--add-data={staged_models_root()}{pathsep()}models")
        runtime = ROOT / "runtime"
        if runtime.exists():
            # runtime/windows/{jev-score.exe, llama.dll, ...} for the offline AI layer
            args.append(f"--add-data={runtime}{pathsep()}runtime")
    # models/jev-style/<release>/build/ 안에는 scorer와 self-contained dylib이
    # 함께 들어 있어 위 add-data 하나로 폐쇄망 bundle이 완성된다.
    jev = ROOT / "models" / "jev-style"
    if jev.exists():
        # The official GGUF runtime ships as data, so PyInstaller's static
        # analysis cannot see the numpy / tokenizers imports it performs.
        args += ["--hidden-import=numpy", "--hidden-import=tokenizers"]
        native = ROOT / "vendor" / "llama.cpp" / "build" / "bin"
        for release in sorted(p for p in jev.iterdir() if p.is_dir()):
            build_dir = release / "build"
            if not (build_dir / "jev-score").exists() and not (build_dir / "jev-score-v2").exists():
                print(f"[warn] {release.name}: scorer not built -> rule-only for this model")
            elif native.exists():
                for lib in native.glob("*.dylib"):
                    dest = build_dir / lib.name
                    if not dest.exists():
                        shutil.copy2(lib, dest)
    if target == "windows":
        args.append("--exclude-module=tkinter")
    if debug:
        args.append("--debug")
        print("[debug build] diagnostics logging enabled")
    pyinstaller_run(args)
    binary = "dist/PrivacyScanner.exe" if target == "windows" else "dist/PrivacyScanner"
    print(f"[ok] built for {target}: {binary}")
    return 0


def pathsep() -> str:
    """PyInstaller uses ';' on Windows and ':' on POSIX inside --add-data."""
    return ";" if sys.platform == "win32" else ":"


def main() -> int:
    parser = argparse.ArgumentParser(description="Privacy Scanner offline build")
    parser.add_argument("--target", choices=["macos", "windows"], required=True)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--verify-only", action="store_true", help="검증만 수행 (빌드하지 않음)")
    parser.add_argument("--skip-tests", action="store_true", help="pytest 생략")
    parser.add_argument("--rule-only", action="store_true", help="모델 없이 규칙 엔진만 번들")
    parser.add_argument("--embed-models", action="store_true",
                        help="GGUF weights inside the onefile EXE (not recommended; "
                             "the base package is then multiple GB)")
    args = parser.parse_args()
    if args.verify_only:
        return 0 if (verify_models() and verify_jev_models() and check_environment()) else 1
    import platform

    current = "macos" if platform.system() == "Darwin" else "windows"
    if args.target != current:
        print(f"[error] {args.target} artifacts must be built on {args.target}; this machine is {current}.")
        return 1
    code = build(args.target, args.debug, skip_tests=args.skip_tests, rule_only=args.rule_only,
                 embed_models=args.embed_models)
    if code == 0:
        report_bundle_size(args.target)
    return code


def report_bundle_size(target: str) -> None:
    binary = ROOT / ("dist/PrivacyScanner.exe" if target == "windows" else "dist/PrivacyScanner")
    if not binary.exists():
        print("[error] built binary not found")
        return
    size_mb = binary.stat().st_size / (1024 ** 2)
    staged = staged_models_root()
    staged_bytes = sum(p.stat().st_size for p in staged.rglob("*") if p.is_file())
    gguf_bytes = sum(p.stat().st_size for p in (ROOT / "models").rglob("*")
                     if p.is_file() and p.suffix.lower() == ".gguf")
    print(f"[size] {binary.name}: {size_mb:.1f} MB "
          f"(bundled scorer/tokenizer/runtime data: {staged_bytes / (1024 ** 2):.1f} MB; "
          f"GGUF weights delivered separately: {gguf_bytes / (1024 ** 2):.1f} MB)")


if __name__ == "__main__":
    raise SystemExit(main())
