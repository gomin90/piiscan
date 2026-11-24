"""Measure the byte breakdown of the Cell verdict ledger at production scale."""
from __future__ import annotations
import sqlite3, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config.settings import AnalysisConfig
from app.core.analysis_service import analyse_file
from app.export.hit_exporter import write_cell_records

path = "results/scale/scale_150000x50.csv"
config = AnalysisConfig(batch_size=1000, use_ai=False, detail_store_path="results/scale/breakdown.sqlite")
started = time.perf_counter()
result = analyse_file(path, config)
print(f"analysis {time.perf_counter()-started:.1f}s, verdict cells {result.cell_counts['verdict_cells']:,}")
write_cell_records([result], "results/scale/privacy_cells_probe.csv")
print(f"cell CSV {Path('results/scale/privacy_cells_probe.csv').stat().st_size/1024/1024:.1f} MB")
conn = sqlite3.connect("results/scale/breakdown.sqlite")
row = conn.execute("""
    SELECT COUNT(*),
           AVG(LENGTH(analysis_id)), AVG(LENGTH(column)), AVG(LENGTH(cell_status)),
           AVG(LENGTH(privacy_types)), AVG(LENGTH(rule_verdict)), AVG(LENGTH(ai_verdict)),
           AVG(LENGTH(final_verdict)), AVG(LENGTH(decision_method)), AVG(LENGTH(masked_value)),
           AVG(LENGTH(evidence))
    FROM cell_verdict""").fetchone()
print("avg char lengths:", row)
print("db file MB:", Path("results/scale/breakdown.sqlite").stat().st_size/1024/1024)
print("table sizes:", conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())
conn.close()
