"""Jev-Style 오프라인 bundle 준비.

scorer 바이너리가 vendor/llama.cpp 절대 rpath에 의존하면 다른 PC(폐쇄망)에서
실행되지 않는다. 이 도구는 native dylib을 scorer旁边에 복사하고 rpath를
@loader_path로 바꿔 bundle을 self-contained 만든다.

    python tools/jev_package.py
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NATIVE_DIR = ROOT / "vendor" / "llama.cpp" / "build" / "bin"
RELEASES = {"0.8b-v3": "jev-score", "2b-v4": "jev-score-v2"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def repo_revision(repo: str) -> dict:
    """Hugging Face revision(sha) + license. 오프라인에서는 None 반환."""
    try:
        from huggingface_hub import HfApi
        info = HfApi().repo_info(repo)
        return {"revision": info.sha,
                "license": (getattr(info, "cardData", {}) or {}).get("license"),
                "last_modified": str(info.last_modified)}
    except Exception as exc:  #network/CLI unavailable -> keep manifest usable offline
        return {"revision": None, "license": None, "error": str(exc)}


def main(argv=None) -> int:
    if not NATIVE_DIR.exists():
        print(f"[error] native libs not found at {NATIVE_DIR}: build llama.cpp first")
        return 1
    manifests = {}
    for key, scorer_name in RELEASES.items():
        release_dir = ROOT / "models" / "jev-style" / key
        build_dir = release_dir / "build"
        scorer = build_dir / scorer_name
        if not scorer.exists():
            print(f"[skip] {key}: {scorer_name} not built")
            continue
        for lib in sorted(NATIVE_DIR.glob("*.dylib")):
            shutil.copy2(lib, build_dir / lib.name)
        rpaths_before = subprocess.run(["otool", "-l", str(scorer)], capture_output=True, text=True).stdout
        if str(NATIVE_DIR) in rpaths_before:
            subprocess.run(["install_name_tool", "-delete_rpath", str(NATIVE_DIR), str(scorer)],
                           check=False, capture_output=True)
        if "@loader_path" not in rpaths_before:
            subprocess.run(["install_name_tool", "-add_rpath", "@loader_path", str(scorer)],
                           check=True, capture_output=True)
        rpaths = subprocess.run(["otool", "-l", str(scorer)], capture_output=True, text=True).stdout
        ok = "@loader_path" in rpaths and str(NATIVE_DIR) not in rpaths
        entries = []
        for path in sorted(build_dir.iterdir()):
            if path.is_file():
                entries.append({"file": path.name, "bytes": path.stat().st_size, "sha256": sha256(path)})
        repo = ROOT / "models" / "jev-style" / key / "manifest.json"
        repo_id = json.loads(repo.read_text())["repo"] if repo.exists() else None
        info = repo_revision(repo_id) if repo_id else {}
        manifest = {
            "repository": repo_id,
            "revision": info.get("revision"),
            "license": info.get("license") or "Apache-2.0",
            "model_version": "0.8B Decision v3" if key == "0.8b-v3" else "2B Decision v4",
            "quantization": "Q4_K_M",
            "scorer": scorer_name,
            "self_contained": ok,
            "llama_cpp_ref": "b94959a64d011915d77a5381c7d4846dcda99530",
            "packaged_at": datetime.now(timezone.utc).isoformat(),
            "total_bytes": sum(e["bytes"] for e in entries),
            "files": entries,
        }
        (release_dir / "package_manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        manifests[key] = {"scorer": scorer_name, "self_contained": ok,
                         "revision": manifest["revision"],
                         "files": len(entries), "total_bytes": manifest["total_bytes"]}
        print(f"[ok] {key}: {scorer_name} rpath self-contained={ok}, {len(entries)} files in build/")
    notices = ROOT / "models" / "jev-style" / "THIRD-PARTY-NOTICES.md"
    notices.write_text(
        "# Third-party notices\n\n"
        "- Jev-Style Decision Models (chaoliangUNSW/Jev-Style-0.8B-Decision-v3-GGUF, "
        "chaoliangUNSW/Jev-Style-2B-Decision-v4-GGUF): Apache-2.0, LICENSE/NOTICE shipped per model dir.\n"
        "- llama.cpp (vendored, native scorer + libllama/libggml dylibs): MIT.\n"
        "- Model GGUF binaries are NOT committed to git; they are produced by tools/jev_download_models.py.\n",
        encoding="utf-8",
    )
    (ROOT / "models" / "jev-style" / "package_manifest.json").write_text(
        json.dumps(manifests, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"notices -> {notices}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
