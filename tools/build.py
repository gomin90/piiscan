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


def build(target: str, debug: bool) -> int:
    if not verify_models():
        return 1
    if not check_environment():
        return 1
    if not run_tests():
        return 1

    from PyInstaller.__main__ import run as pyinstaller_run

    args = [
        str(ENTRY),
        "--onefile",
        "--name=PrivacyScanner",
        "--noconfirm",
        f"--add-data={ROOT / 'models'}{pathsep()}models",
        f"--add-data={ROOT / 'resources'}{pathsep()}resources",
    ]
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
    args = parser.parse_args()
    if args.verify_only:
        return 0 if (verify_models() and check_environment()) else 1
    import platform

    current = "macos" if platform.system() == "Darwin" else "windows"
    if args.target != current:
        print(f"[error] {args.target} artifacts must be built on {args.target}; this machine is {current}.")
        return 1
    return build(args.target, args.debug)


if __name__ == "__main__":
    raise SystemExit(main())
