"""폐쇄망 검증: 네트워크 소켓을 차단한 상태에서 AI 판정이 정상 작동하는지实测."""
from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.ai.jev.adapter import JevStyleAdapter, build_column_state, PERSONALITY_QUESTION, TYPE_QUESTION
from app.ai.jev.decision import JevStyleDecisionRequest, PERSONALITY_OPTIONS, TYPE_OPTIONS
from app.ai.jev.model_manager import JevStyleModelManager
from app.ai.gpu_detector import detect_hardware


class BlockedSocket(socket.socket):
    def connect(self, *args, **kwargs):
        raise OSError("network disabled for offline verification")


def main() -> int:
    # 폐쇄망 시뮬레이션: 모든 socket connect 차단 + HF env offline
    socket.socket = BlockedSocket
    import os
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"

    manager = JevStyleModelManager()
    hardware = detect_hardware()
    results = []
    for key in ("0.8b-v3", "2b-v4"):
        spec = manager.select(hardware.available_ram_gb, key)
        adapter = JevStyleAdapter(spec, hardware, threads=4)
        loaded = adapter.load()
        decision = None
        if loaded:
            state = build_column_state({"schema": "TB", "table": "T", "column": "MOBILE",
                "stats": {"rows": 10, "non_empty": 10, "null": 0, "distinct_estimate": 10,
                          "samples": ["01012345678", "01098765432"]}})
            try:
                out = adapter.decide_many([
                    JevStyleDecisionRequest("personality", state, PERSONALITY_QUESTION, PERSONALITY_OPTIONS, "choice", "general"),
                    JevStyleDecisionRequest("type", state, TYPE_QUESTION, TYPE_OPTIONS, "choice", "intent"),
                ])
                decision = [o.as_dict() for o in out]
            except Exception as exc:
                decision = {"error": str(exc)}
        adapter.close()
        results.append({"model": key, "loaded_offline": loaded, "status": adapter.status,
                       "backend": adapter.backend, "decisions": decision,
                       "scorer": str(spec.scorer_path) if spec and spec.scorer_path else None,
                       "sha256": spec.sha256 if spec else None,
                       "verified": spec.verified if spec else False})
        print(f"{key}: loaded_offline={loaded} status={adapter.status}")
        if decision and not isinstance(decision, dict):
            for d in decision:
                print("   ", d["scenario"], d["decision"], round(d["confidence"], 3), "tokens", d["input_tokens"])
    Path("results/qa/jev_offline_check.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print("written -> results/qa/jev_offline_check.json")
    return 0 if all(r["loaded_offline"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
