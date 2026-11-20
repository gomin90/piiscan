#!/usr/bin/env python3
"""Generate the distributable offline bundle ZIP (GitHub Release asset).

GitHub's "Download ZIP" only ships Git LFS *pointers*, so the closed-network
customer needs a real archive. This script builds one from the manifest:
models, tokenizer, native Windows runtime, wheelhouse, app code, scripts, docs.

    python tools/make_release_bundle.py
    python tools/make_release_bundle.py --rule-only      # no GGUF models
    python tools/make_release_bundle.py --no-wheels
"""
from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "manifests" / "offline-bundle-manifest.json"
STORE_SUFFIXES = (".gguf", ".whl", ".dll", ".exe", ".dylib", ".zip")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def build(output: Path, include_models: bool, include_wheels: bool) -> int:
    if not MANIFEST.is_file():
        print("Run tools/make_bundle_manifest.py first.")
        return 1
    manifest = json.loads(read_text(MANIFEST))
    entries = [e for e in manifest["files"] if (ROOT / e["path"]).is_file()]

    if not include_models:
        entries = [e for e in entries
                   if not (e["category"] == "model" and e["size"] > 1_000_000)]
    if not include_wheels:
        entries = [e for e in entries if e["category"] != "wheelhouse"]

    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for entry in entries:
            path = ROOT / entry["path"]
            compress = zipfile.ZIP_STORED if path.suffix in STORE_SUFFIXES else zipfile.ZIP_DEFLATED
            archive.write(path, entry["path"], compress_type=compress)
        readme = f"""Privacy Scanner offline bundle
Built: {date.today().isoformat()}
Git commit: {manifest.get('git', {}).get('commit', 'unknown')}
Files: {len(entries)} ({manifest['total_mb']} MB)

Verify integrity on the target machine:
  powershell -ExecutionPolicy Bypass -File scripts\\windows\\verify-offline-bundle.ps1
  powershell -ExecutionPolicy Bypass -File scripts\\windows\\diagnose.ps1

Run:
  scripts\\windows\\run.bat --file samples\\windows_sample.csv --debug --report-json results\\windows\\report.json

See docs/windows-test-guide.md.
"""
        archive.writestr("BUNDLE-README.txt", readme)
    size = output.stat().st_size / (1024 ** 2)
    print(f"[ok] {output.name}: {size:.1f} MB, {len(entries)} files")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline release bundle")
    parser.add_argument("--output", default="dist/piiscan-offline-bundle.zip")
    parser.add_argument("--rule-only", action="store_true", help="exclude GGUF model files")
    parser.add_argument("--no-wheels", action="store_true", help="exclude the wheelhouse")
    args = parser.parse_args()
    return build(ROOT / args.output, include_models=not args.rule_only, include_wheels=not args.no_wheels)


if __name__ == "__main__":
    raise SystemExit(main())
