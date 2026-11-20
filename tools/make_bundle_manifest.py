#!/usr/bin/env python3
"""SHA256 manifest 생성: 오프라인 배포 번들의 모든 파일 목록 + 해시.

Windows 쪽 verify-offline-bundle.ps1 가 같은 파일을 Get-FileHash 로 대조한다.
대용량 모델(GGUF), wheelhouse, DLL, scorer 바이너리까지 모두 포함한다.

Usage:
    python tools/make_bundle_manifest.py
    python tools/make_bundle_manifest.py --verify
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

INCLUDE_DIRS = (
    "app",
    "resources",
    "scripts",
    "docs",
    "requirements",
    "tests/fixtures",
    "models/jev-style",
    "runtime/windows",
    "vendor/wheels",
)

MANIFEST_PATH = "manifests/offline-bundle-manifest.json"

SKIP_PARTS = {".venv", "venv", "dist", "build", "results", "__pycache__", ".git"}
SKIP_SUFFIXES = (".pyc", ".log")

CATEGORIES = {
    "models/jev-style": "model",
    "runtime/windows": "windows_runtime",
    "vendor/wheels": "wheelhouse",
    "scripts": "scripts",
    "docs": "docs",
    "requirements": "requirements",
    "resources": "resources",
    "tests/fixtures": "fixtures",
    "app": "app",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def category(rel: Path) -> str:
    for prefix, name in CATEGORIES.items():
        if rel.parts[0] == prefix.split("/")[0] and str(rel).startswith(prefix):
            return name
    return "other"


def collect() -> list[dict]:
    """Manifest covers exactly the files that ship in the repo/bundle (git-tracked)."""
    try:
        tracked = subprocess.run(
            ["git", "ls-files"], capture_output=True, text=True, check=True, cwd=ROOT
        ).stdout.splitlines()
    except Exception:
        tracked = None

    paths: list[Path] = []
    if tracked:
        paths = [ROOT / rel for rel in tracked]
    else:
        for base in INCLUDE_DIRS:
            directory = ROOT / base
            if directory.is_dir():
                paths.extend(directory.rglob("*"))

    entries = []
    for path in sorted(paths):
        if not path.is_file() or path.name == ".gitkeep":
            continue
        rel = path.relative_to(ROOT)
        if str(rel) == MANIFEST_PATH:
            continue  # a manifest cannot contain its own hash
        if any(part in SKIP_PARTS for part in rel.parts):
            continue
        if path.suffix in SKIP_SUFFIXES:
            continue
        entries.append(
            {
                "path": str(rel),
                "size": path.stat().st_size,
                "sha256": sha256(path),
                "category": category(rel),
            }
        )
    return entries


def git_info() -> dict:
    def run(*cmd):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, check=True, cwd=ROOT).stdout.strip()
        except Exception:
            return ""

    sha = run("git", "rev-parse", "HEAD")
    return {
        "commit": sha,
        "branch": run("git", "rev-parse", "--abbrev-ref", "HEAD"),
        "repository": run("git", "remote", "get-url", "origin"),
        # A manifest cannot hash itself, so the commit that contains this file is
        # always the *next* one; consumers compare file hashes, not this field.
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


MODEL_SUMMARY = {
    "0.8b-v3": {
        "repo": "chaoliangUNSW/Jev-Style-0.8B-Decision-v3-GGUF",
        "model": "Jev-Style-0.8B-Decision-v3",
        "quant": "Q4_K_M",
        "scorer": "jev-score.cpp",
    },
    "2b-v4": {
        "repo": "chaoliangUNSW/Jev-Style-2B-Decision-v4-GGUF",
        "model": "Jev-Style-2B-Decision-v4",
        "quant": "Q4_K_M",
        "scorer": "jev_score_v2.cpp",
    },
}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Offline bundle SHA256 manifest")
    parser.add_argument("--output", default="manifests/offline-bundle-manifest.json")
    parser.add_argument("--verify", action="store_true", help="verify existing manifest instead of writing")
    args = parser.parse_args(argv)

    entries = collect()
    total = sum(entry["size"] for entry in entries)
    manifest = {
        "schema": "piiscan-offline-bundle/1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git": git_info(),
        "target": {
            "windows": {"os": "Windows 10/11 x64", "python": "3.12 x64 (recommended)", "note": "cross-built on macOS, NOT executed on Windows yet"},
            "macos": {"os": "macOS (Apple Silicon verified)", "python": sys.version.split()[0]},
        },
        "models": MODEL_SUMMARY,
        "file_count": len(entries),
        "total_bytes": total,
        "total_mb": round(total / (1024 * 1024), 1),
        "files": entries,
    }

    out = ROOT / args.output
    if args.verify:
        if not out.is_file():
            print(f"manifest missing: {out}")
            return 1
        existing = json.loads(out.read_text(encoding="utf-8"))
        bad = [e for e in existing["files"] if sha256(ROOT / e["path"]) != e["sha256"]]
        missing = [e for e in existing["files"] if not (ROOT / e["path"]).is_file()]
        print(f"manifest entries: {existing['file_count']}  mismatches: {len(bad)}  missing: {len(missing)}")
        for e in bad[:10]:
            print("  MISMATCH", e["path"])
        for e in missing[:10]:
            print("  MISSING", e["path"])
        return 1 if bad or missing else 0

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{out}: {len(entries)} files, {manifest['total_mb']} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
