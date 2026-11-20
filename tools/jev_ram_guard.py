"""RAM 한계(8GB/4GB/2GB)에서 Jev-Style 모델 선택/생략 실측.

`manager.select()`와 `adapter.load()` 경로를 simulated HardwareReport로 실행하여
"8GB机器에서 2B 실행 가능한지"를 실행 근거로 판단한다.

    python tools/jev_ram_guard.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.ai.jev.adapter import JevStyleAdapter
from app.ai.jev.model_manager import JevStyleModelManager


class SimulatedHardware:
    def __init__(self, available_ram_gb: float, backend: str = "cpu") -> None:
        self.available_ram_gb = available_ram_gb
        self.total_ram_gb = available_ram_gb + 2.0
        self.backend = backend
        self.cpu_count = 8
        self.gpu_model = ""
        self.vram_total_gb = 0.0


def main() -> int:
    specs = {spec.key: spec for spec in JevStyleModelManager().discover()}
    rows = []
    for ram in (8.0, 6.0, 4.0, 2.0):
        for backend in ("cpu", "metal"):
            selected = JevStyleModelManager().select(available_ram_gb=ram)
            for key in ("0.8b-v3", "2b-v4"):
                if key not in specs:
                    continue
                adapter = JevStyleAdapter(specs[key], SimulatedHardware(ram, backend))
                loaded = adapter.load()
                rows.append({"available_ram_gb": ram, "backend": backend, "model": key,
                            "auto_selected": selected.key if selected else None,
                            "load_ok": loaded, "status": adapter.status})
                adapter.close()
    out = ROOT / "results" / "qa" / "jev_ram_guard.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    for row in rows:
        print(f"RAM {row['available_ram_gb']}GB {row['backend']:6s} {row['model']:8s} "
              f"auto={row['auto_selected']} load={row['load_ok']} :: {row['status']}")
    print("written ->", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
