"""Measure candidate Cell-ledger layouts at 150k-row production scale."""
from __future__ import annotations
import csv, os, random, sqlite3, time
from pathlib import Path

ROWS, COLS = 300_000, 20   # 6M cells - the design target's lower bound
rng = random.Random(3)
STATUSES = [1, 2, 3, 4, 5]  # 1 빈 셀, 2 KEY 제외, 3 개인정보, 4 검토, 5 비개인정보

def build(rows: int, cols: int):
    for r in range(rows):
        for c in range(cols):
            status = rng.choices(STATUSES, weights=[10, 15, 18, 12, 45])[0]
            yield (r + 1, c, status, rng.random(), "규칙: 코드 값" if status == 5 else "")

def layout(path: str, ddl: str, insert: str, rows_gen):
    p = Path(path)
    if p.exists():
        p.unlink()
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(ddl)
    t = time.perf_counter()
    batch = []
    total = 0
    for row in rows_gen:
        batch.append(row)
        total += 1
        if len(batch) >= 20000:
            conn.executemany(insert, batch)
            batch = []
    if batch:
        conn.executemany(insert, batch)
    conn.commit()
    size = os.path.getsize(path)
    conn.close()
    return {"seconds": round(time.perf_counter() - t, 2), "cells": total,
            "db_mb": round(size / 1024 / 1024, 1), "bytes_per_cell": round(size / total, 1)}

print("A: full text ledger (current style)")
print(layout("results/scale/a.sqlite",
    "CREATE TABLE v (analysis TEXT, row INTEGER, col INTEGER, status TEXT, types TEXT, "
    "rule TEXT, ai TEXT, final TEXT, conf REAL, method TEXT, review INTEGER, masked TEXT, evidence TEXT, "
    "PRIMARY KEY (analysis, row, col))",
    "INSERT OR REPLACE INTO v VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
    ((f"anal-{ROWS}x{COLS}", r, c, ["빈 셀","KEY 제외","개인정보","검토 필요","비개인정보"][s-1],
      "", "규칙: 코드", "", "", conf, "규칙", 0, "", ev) for r, c, s, conf, ev in build(ROWS, COLS))))

print("B: numeric status + detail only for verdict cells")
print(layout("results/scale/b.sqlite",
    "CREATE TABLE v (analysis INTEGER, row INTEGER, col INTEGER, status INTEGER, conf REAL, evidence TEXT, "
    "PRIMARY KEY (analysis, row, col))",
    "INSERT OR REPLACE INTO v VALUES (?,?,?,?,?,?)",
    ((1, r, c, s, conf, ev if s in (3, 4) else "") for r, c, s, conf, ev in build(ROWS, COLS))))

print("C: derived empty/key - store only cells with an actual verdict")
print(layout("results/scale/c.sqlite",
    "CREATE TABLE v (analysis INTEGER, row INTEGER, col INTEGER, status INTEGER, conf REAL, evidence TEXT, "
    "PRIMARY KEY (analysis, row, col))",
    "INSERT OR REPLACE INTO v VALUES (?,?,?,?,?,?)",
    ((1, r, c, s, conf, ev) for r, c, s, conf, ev in build(ROWS, COLS) if s >= 3)))

# CSV ledger throughput
out = Path("results/scale/c_cells.csv")
t = time.perf_counter()
with open(out, "w", encoding="utf-8-sig", newline="") as handle:
    writer = csv.writer(handle)
    writer.writerow(["행번호", "컬럼 인덱스", "판정", "근거"])
    for r, c, s, conf, ev in build(ROWS, COLS):
        writer.writerow([r, c, ["빈 셀", "KEY 제외", "개인정보", "검토 필요", "비개인정보"][s - 1], ev])
print(f"CSV export {total} cells: {round(time.perf_counter()-t,2)}s, {round(out.stat().st_size/1024/1024,1)} MB, "
      f"{round(out.stat().st_size/total,1)} B/cell")
