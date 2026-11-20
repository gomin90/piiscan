"""Jev-Style 모델 + scorer을 개발 환경에서 확보(다운로드/빌드)하는 도구.

최종 제품 실행 시 네트워크 접근은 없다: 이 스크립트가 한 번 실행되어
models/jev-style/<release>/ 에 GGUF, tokenizer, config, manifest, scorer
바이너리를 준비하고, 이후 JevStyleModelManager는 로컬 파일만 사용한다.

`python tools/jev_download_models.py [--force]`
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LLAMA_REF = "b94959a64d011915d77a5381c7d4846dcda99530"

RELEASES = {
    "0.8b-v3": {
        "repo": "chaoliangUNSW/Jev-Style-0.8B-Decision-v3-GGUF",
        "include": ["*Q4_K_M.gguf", "tokenizer/*.json", "readout_config.json",
                    "release_config.json", "manifest.json", "LICENSE", "NOTICE",
                    "jev_score.cpp", "build_jev_score.sh", "jev_style_decision_gguf.py"],
        "scorer_src": "jev_score.cpp",
        "scorer_bin": "jev-score",
    },
    "2b-v4": {
        "repo": "chaoliangUNSW/Jev-Style-2B-Decision-v4-GGUF",
        "include": ["*Q4_K_M.gguf", "tokenizer/*.json", "readout_config.json",
                    "release_config.json", "manifest.json", "LICENSE", "NOTICE",
                    "jev_score_v2.cpp", "build_jev_score_v2.sh", "jev_style_decision_gguf.py"],
        "scorer_src": "jev_score_v2.cpp",
        "scorer_bin": "jev-score-v2",
    },
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(key: str, force: bool = False) -> Path:
    release = RELEASES[key]
    target = ROOT / "models" / "jev-style" / key
    target.mkdir(parents=True, exist_ok=True)
    gguf = target / next(iter([p for p in target.glob("*Q4_K_M.gguf")]), Path("none"))
    if gguf.exists() and not force:
        print(f"[skip download] {key}: {gguf.name} already present")
        return target
    command = [
        "hf", "download", release["repo"],
        "--include", *release["include"],
        "--local-dir", str(target),
    ]
    print(" ".join(command))
    subprocess.run(command, check=True)
    return target


def build_scorer(key: str) -> Path | None:
    release = RELEASES[key]
    target = ROOT / "models" / "jev-style" / key
    binary = target / "build" / release["scorer_bin"]
    if binary.exists():
        return binary
    llama = ROOT / "vendor" / "llama.cpp"
    if not llama.exists():
        print(f"[scorer build skipped] vendor/llama.cpp 없음: git clone + build 필요")
        return None
    script = target / (f"build_{release['scorer_bin']}.sh")
    if script.exists():
        print(f"official build script: {script}")
        subprocess.run(["bash", str(script)], cwd=str(target), check=False)
    if binary.exists():
        return binary
    print(f"[manual build] {release['scorer_src']} -> {binary}")
    subprocess.run(
        ["cc", "-O2", "-std=c++17", str(target / release["scorer_src"]), "-o", str(binary),
         "-I", str(llama / "include"), str(llama / "build" / "libllama.dylib"),
         "-Wl,-rpath," + str(llama / "build")],
        check=True,
    )
    return binary


def write_manifest(key: str) -> dict:
    target = ROOT / "models" / "jev-style" / key
    files = []
    for path in sorted(target.rglob("*")):
        if not path.is_file():
            continue
        parts = path.parts
        if "build" in parts or ".cache" in parts or "__pycache__" in parts:
            continue
        if path.name == "package_manifest.json":
            continue
        files.append({"file": str(path.relative_to(target)), "bytes": path.stat().st_size,
                      "sha256": sha256(path)})
    revision = ""
    release_config = target / "release_config.json"
    if release_config.exists():
        try:
            revision = json.loads(release_config.read_text(encoding="utf-8")).get("revision", "")
        except json.JSONDecodeError:
            revision = ""
    manifest = {
        "repository": RELEASES[key]["repo"],
        "revision": revision,
        "model_version": "0.8B Decision v3" if key == "0.8b-v3" else "2B Decision v4",
        "quantization": "Q4_K_M",
        "scorer": RELEASES[key]["scorer_bin"],
        "llama_cpp_ref": LLAMA_REF,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "files": files,
    }
    path = target / "package_manifest.json"
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--models", nargs="*", default=list(RELEASES))
    args = parser.parse_args(argv)
    for key in args.models:
        download(key, args.force)
        build_scorer(key)
        manifest = write_manifest(key)
        total = sum(item["bytes"] for item in manifest["files"])
        print(f"{key}: {len(manifest['files'])} files, {total / 1024**2:.1f} MB")
        for item in manifest["files"]:
            print(f"  {item['file']:45s} {item['bytes']:>12d}  {item['sha256'][:16]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
