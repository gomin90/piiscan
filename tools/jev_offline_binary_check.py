"""PyInstaller frozen binary의 폐쇄망 실행 검증.

dist/PrivacyScanner(または .exe)를 실행하면서 과정 전체의 TCP connection을
psutil로 감시한다. 모델/scorer이 로컬(_MEIPASS) 파일로부터 로드되는 반면
외부 connection이 0건이어야 '오프라인 배포' 통과로 기록할 수 있다.

    python tools/jev_offline_binary_check.py [--binary dist/PrivacyScanner]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import psutil


def connection_snapshot(procs: list[psutil.Process]) -> list[dict]:
    found = []
    for proc in procs:
        try:
            for conn in proc.net_connections(kind="TCP"):
                if conn.status == psutil.ConnectionStatus.CLOSED:
                    continue
                found.append({"pid": proc.pid, "status": conn.status,
                              "local": str(conn.local_address), "remote": str(conn.remote_address)})
        except Exception:
            continue
    return found


def run(binary: Path, csv: str, model: str, interval: float = 0.1) -> dict:
    if not binary.exists():
        return {"error": f"binary not found: {binary}"}
    started = time.time()
    proc = subprocess.Popen([str(binary), "--file", csv, "--mode", "full",
                             "--output-dir", "results/offline_check", "--jev-model", model, "--json"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    connections: list[dict] = []
    peak_rss_mb = 0.0
    while proc.poll() is None:
        procs = [psutil.Process(proc.pid)]
        try:
            procs.extend(psutil.Process(proc.pid).children(recursive=True))
        except Exception:
            pass
        for p in procs:
            try:
                peak_rss_mb = max(peak_rss_mb, p.memory_info().rss / 1024 ** 2)
            except Exception:
                pass
        connections.extend(connection_snapshot(procs))
        time.sleep(interval)
    stdout, stderr = proc.stdout.read().decode(errors="replace"), proc.stderr.read().decode(errors="replace")
    ai_summary = {}
    try:
        ai_summary = json.loads(stdout).get("ai", {})
    except json.JSONDecodeError:
        pass
    return {
        "binary": str(binary),
        "binary_mb": round(binary.stat().st_size / 1024 ** 2, 1),
        "exit_code": proc.returncode,
        "wall_seconds": round(time.time() - started, 2),
        "external_tcp_connections": len(connections),
        "connections": connections[:10],
        "ai": {k: ai_summary.get(k) for k in ("model", "backend", "status", "decisions", "failures", "peak_rss_mb", "skipped_reason")},
        "stderr_tail": stderr[-400:],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", default="dist/PrivacyScanner")
    parser.add_argument("--csv", default="tests/fixtures/synthetic/mixed_pii.csv")
    parser.add_argument("--model", default="0.8b-v3")
    args = parser.parse_args(argv)
    result = run(ROOT / args.binary if not Path(args.binary).is_absolute() else Path(args.binary),
                 args.csv, args.model)
    out = ROOT / "results" / "qa" / "jev_offline_binary_check.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print("written ->", out)
    return 0 if result.get("exit_code") == 0 and result.get("external_tcp_connections") == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
