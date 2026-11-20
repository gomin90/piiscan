#!/usr/bin/env python3
"""Generate the distributable offline bundle ZIP (GitHub Release asset).

GitHub's "Download ZIP" only ships Git LFS *pointers*, so the closed-network
customer needs a real archive. This script builds one from the manifest:
models, tokenizer, native Windows runtime, wheelhouse, app code, scripts, docs.

    python tools/make_release_bundle.py
    python tools/make_release_bundle.py --rule-only      # no GGUF models
    python tools/make_release_bundle.py --no-wheels
    python tools/make_release_bundle.py --split          # 3 GitHub-asset-sized parts

GitHub caps a single release asset at 2 GiB, and the whole bundle is ~2.26 GB, so
`--split` writes three archives that unpack into the same tree:
  piiscan-app.zip        code, runtime DLLs/EXEs, wheelhouse scripts, docs, manifest
  piiscan-model-0.8b.zip  GGUF + tokenizer for the 0.8B v3 model
  piiscan-model-2b.zip    GGUF + tokenizer for the 2B v4 model
Unzip all three into the same folder, then run verify-offline-bundle.ps1.
"""
from __future__ import annotations

import argparse
import json
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "manifests" / "offline-bundle-manifest.json"
STORE_SUFFIXES = (".gguf", ".whl", ".dll", ".exe", ".dylib", ".zip")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def manifest_entries(manifest: dict) -> list[dict]:
    return [e for e in manifest["files"] if (ROOT / e["path"]).is_file()]


def write_archive(output: Path, entries: list[dict], manifest: dict, note: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    # The manifest cannot hash itself, so it is not one of its own entries; ship it
    # anyway so the offline verifier has something to check against.
    extra = []
    if MANIFEST.is_file() and not any(e["path"] == "manifests/offline-bundle-manifest.json" for e in entries):
        extra = [{"path": "manifests/offline-bundle-manifest.json", "size": MANIFEST.stat().st_size}]
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for entry in entries + extra:
            path = ROOT / entry["path"]
            compress = zipfile.ZIP_STORED if path.suffix in STORE_SUFFIXES else zipfile.ZIP_DEFLATED
            archive.write(path, entry["path"], compress_type=compress)
        readme = f"""Privacy Scanner offline bundle
Built: {date.today().isoformat()}
Git commit: {manifest.get('git', {}).get('commit', 'unknown')}
Part: {output.name}
Files: {len(entries)} ({sum(e['size'] for e in entries) / 1024**2:.1f} MB)
{note}

Verify integrity on the target machine:
  powershell -ExecutionPolicy Bypass -File scripts\\windows\\verify-offline-bundle.ps1
  powershell -ExecutionPolicy Bypass -File scripts\\windows\\diagnose.ps1

Run:
  scripts\\windows\\run.bat --file samples\\windows_sample.csv --debug --report-json results\\windows\\report.json

See docs/windows-test-guide.md.
"""
        archive.writestr("BUNDLE-README.txt", readme)
    print(f"[ok] {output.name}: {output.stat().st_size / 1024**2:.1f} MB, {len(entries)} files")


def build(output: Path, manifest: dict, include_models: bool, include_wheels: bool) -> int:
    entries = manifest_entries(manifest)
    if not include_models:
        entries = [e for e in entries if not (e["category"] == "model" and e["size"] > 1_000_000)]
    if not include_wheels:
        entries = [e for e in entries if e["category"] != "wheelhouse"]
    write_archive(output, entries, manifest, "Complete bundle in one archive.")
    return 0


def build_split(base: Path, manifest: dict) -> int:
    """Three archives, each under GitHub's 2 GiB asset limit, same directory tree."""
    entries = manifest_entries(manifest)
    model_dirs = ("models/jev-style/0.8b-v3/", "models/jev-style/2b-v4/")
    parts = {
        "piiscan-app.zip": [e for e in entries if not e["path"].startswith(model_dirs)],
        "piiscan-model-0.8b.zip": [e for e in entries if e["path"].startswith("models/jev-style/0.8b-v3/")],
        "piiscan-model-2b.zip": [e for e in entries if e["path"].startswith("models/jev-style/2b-v4/")],
    }
    for name, part in parts.items():
        if not part:
            print(f"[fail] {name} would be empty: models/ or runtime/ is missing")
            return 1
        write_archive(base / name, part, manifest,
                      "Unzip every part into the same folder before running the scripts.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline release bundle")
    parser.add_argument("--output", default="dist/piiscan-offline-bundle.zip")
    parser.add_argument("--rule-only", action="store_true", help="exclude GGUF model files")
    parser.add_argument("--no-wheels", action="store_true", help="exclude the wheelhouse")
    parser.add_argument("--split", action="store_true", help="write 3 parts, each under GitHub's 2 GiB limit")
    args = parser.parse_args()
    if not MANIFEST.is_file():
        print("Run tools/make_bundle_manifest.py first.")
        return 1
    manifest = json.loads(read_text(MANIFEST))
    if args.split:
        return build_split(ROOT / "dist", manifest)
    return build(ROOT / args.output, manifest, include_models=not args.rule_only,
                 include_wheels=not args.no_wheels)


if __name__ == "__main__":
    raise SystemExit(main())
