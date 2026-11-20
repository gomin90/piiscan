"""Platform build wrapper.

    python tools/build.py --target macos
    python tools/build.py --target windows --debug

Builds a single-file executable with PyInstaller and bundles the GGUF model,
resources and native libraries. Model integrity is verified with SHA256.
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


def build(target: str, debug: bool, skip_tests: bool = False, rule_only: bool = False) -> int:
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
        args.append(f"--add-data={ROOT / 'models'}{pathsep()}models")
    # models/jev-style/<release>/build/ 안에는 scorer와 self-contained dylib이
    # 함께 들어 있어 위 add-data 하나로 폐쇄망 bundle이 완성된다.
    jev = ROOT / "models" / "jev-style"
    if jev.exists():
        # 공식 GGUF runtime은 data 파일로 번들되므로 PyInstaller의静态 분석이
        # numpy/tokenizersimport를 못 finds. hidden-import로 Explicit히 포함한다.
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
    args = parser.parse_args()
    if args.verify_only:
        return 0 if (verify_models() and verify_jev_models() and check_environment()) else 1
    import platform

    current = "macos" if platform.system() == "Darwin" else "windows"
    if args.target != current:
        print(f"[error] {args.target} artifacts must be built on {args.target}; this machine is {current}.")
        return 1
    code = build(args.target, args.debug, skip_tests=args.skip_tests, rule_only=args.rule_only)
    if code == 0:
        report_bundle_size(args.target)
    return code


def report_bundle_size(target: str) -> None:
    binary = ROOT / ("dist/PrivacyScanner.exe" if target == "windows" else "dist/PrivacyScanner")
    if not binary.exists():
        print("[error] built binary not found")
        return
    size_mb = binary.stat().st_size / (1024 ** 2)
    model_bytes = 0
    jev = ROOT / "models" / "jev-style"
    if jev.exists():
        model_bytes = sum(p.stat().st_size for p in jev.rglob("*") if p.is_file())
    print(f"[size] {binary.name}: {size_mb:.1f} MB "
          f"(bundled Jev-Style model+scorer data: {model_bytes / (1024 ** 2):.1f} MB)")


if __name__ == "__main__":
    raise SystemExit(main())
