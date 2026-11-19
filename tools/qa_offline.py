#!/usr/bin/env python3
"""Air-gap verification: run the scanner with all network syscalls blocked and
with the frozen binary, and record whether any connection is attempted.

    python tools/qa_offline.py [--binary dist/PrivacyScanner]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import psutil


NETWORK_SYNONYMS = ("urllib.request", "http.client", "requests", "socket.create_connection",
                    "urlopen", "httpx", "aiohttp")


def static_scan() -> dict:
    """Every module in app/ is checked for network API usage."""
    findings = []
    for path in sorted(ROOT.glob("app/**/*.py")):
        text = path.read_text(encoding="utf-8")
        for token in NETWORK_SYNONYMS:
            if token in text:
                findings.append({"file": str(path.relative_to(ROOT)), "token": token})
    return {"files_scanned": len(list(ROOT.glob("app/**/*.py"))), "network_calls": findings}


def blocked_run(fixture: Path, output: Path) -> dict:
    """Run the analysis inside a process that refuses every socket."""
    script = f"""
import socket, sys
class Blocked:
    def __init__(self, *a, **k):
        raise OSError("air-gap: network access blocked")
socket.socket = Blocked
socket.create_connection = Blocked
socket.getaddrinfo = Blocked
sys.path.insert(0, {str(ROOT)!r})
from app.main import main
sys.argv = ["piiscan", "--file", {str(fixture)!r}, "--output-dir", {str(output)!r}, "--no-ai"]
raise SystemExit(main())
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    return {
        "returncode": result.returncode,
        "stdout": result.stdout[-800:],
        "stderr": result.stderr[-1200:],
        "reports": sorted(p.name for p in output.glob("*")),
    }


def frozen_run(binary: Path, fixture: Path, output: Path) -> dict:
    if not binary.exists():
        return {"error": f"{binary} not built; run tools/build.py --target macos"}
    # Minimal environment: no PYTHONPATH/PYTHONHOME, proving the executable does
    # not depend on a system Python installation.
    env = {"PATH": os.environ.get("PATH", "")}
    started = time.time()
    process = subprocess.Popen([str(binary), "--file", str(fixture), "--output-dir", str(output), "--no-ai"],
                               env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    connections = set()
    rss_peak = 0
    while process.poll() is None:
        try:
            procs = [psutil.Process(process.pid)] + psutil.Process(process.pid).children(recursive=True)
            for proc in procs:
                for conn in proc.net_connections():
                    connections.add((conn.type, conn.raddr))
                rss_peak = max(rss_peak, proc.memory_info().rss)
        except Exception:
            pass
        time.sleep(0.05)
    stdout, stderr = process.communicate()
    return {
        "binary": str(binary),
        "binary_size_mb": round(binary.stat().st_size / (1024 ** 2), 1),
        "file_type": subprocess.run(["file", str(binary)], capture_output=True, text=True).stdout.strip(),
        "returncode": process.returncode,
        "seconds": round(time.time() - started, 2),
        "peak_rss_mb": round(rss_peak / (1024 ** 2), 1),
        "network_connections": sorted(map(str, connections)),
        "stdout": stdout[-800:],
        "stderr": stderr[-1200:],
        "reports": sorted(p.name for p in output.glob("*")),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", default=str(ROOT / "dist" / "PrivacyScanner"))
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT))
    from tests.fixtures.generate_fixtures import FIXTURE_DIR, generate

    generate()
    fixture = FIXTURE_DIR / "mixed_pii.csv"
    out_dir = ROOT / "results" / "offline"
    out_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "static_scan": static_scan(),
        "blocked_source_run": blocked_run(fixture, out_dir),
        "frozen_binary_run": frozen_run(Path(args.binary), fixture, out_dir),
        "model_bundle": {
            "manifest": json.loads((ROOT / "models" / "model_manifest.json").read_text(encoding="utf-8")),
            "gguf_present": bool(list((ROOT / "models").glob("*.gguf"))),
        },
        "vendor_wheels_present": bool(list((ROOT / "vendor" / "wheels").glob("*"))) if (ROOT / "vendor").exists() else False,
    }
    path = ROOT / "results" / "qa_offline.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
