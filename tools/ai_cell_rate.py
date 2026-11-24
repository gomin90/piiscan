"""Measure real Jev-Style Cell inference throughput at production scale."""
from __future__ import annotations
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.ai import create_ai_engine
from app.ai.gpu_detector import detect_hardware
from app.config.settings import AnalysisConfig
from app.core.analysis_service import analyse_file

config = AnalysisConfig(batch_size=1000, use_ai=True, max_ai_cell_reviews=5000,
                        ai_cell_reviews="selective", ai_cell_batch_size=25,
                        max_ai_column_reviews=50, max_ai_row_reviews=50)
hardware = detect_hardware()
llm = create_ai_engine(config, hardware)
started = time.perf_counter()
result = analyse_file("results/scale/scale_150000x20.csv", config, llm)
elapsed = time.perf_counter() - started
audit = result.ai_audit
cell = audit.get("cell", {})
print("hardware:", hardware.backend, hardware.gpu_model)
print("total seconds:", round(elapsed, 1), "rule:", result.timings.get("rules"))
print("cell AI audit:", cell)
if cell.get("attempted"):
    print("cells per AI second:", int(cell["attempted"] / cell["seconds"]) if cell.get("seconds") else 0)
print("ai summary:", result.ai_summary.get("model"), result.ai_summary.get("backend"))
