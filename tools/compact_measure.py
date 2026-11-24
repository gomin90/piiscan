"""Measure SQLite row-size/insert-rate options for the Cell verdict ledger."""
from __future__ import annotations
import sqlite3, time, os
from pathlib import Path

CELLS = 10_000_000

def bench(path: str, ddl: str, make_row) -> dict:
    p = Path(path)
    if p.exists():
        p.unlink()
    conn = sqlite3.connect(str(p))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(ddl)
    started = time.perf_counter()
    batch = []
    for i in range(CELLS):
        batch.append(make_row(i))
        if len(batch) >= 5000:
            conn.executemany(
                "INSERT INTO t (a,b,c,d,e,f) VALUES (?,?,?,?,?,?)", batch) if "t" == "t" else None
            batch = []
    if batch:
        conn.executemany("INSERT INTO t (a,b,c,d,e,f) VALUES (?,?,?,?,?,?)", batch)
    conn.commit()
    elapsed = time.perf_counter() - started
    size = p.stat().st_size
    conn.close()
    return {"seconds": round(elapsed, 2), "cells_per_second": int(CELLS / elapsed),
            "db_mb": round(size / 1024 / 1024, 1), "bytes_per_cell": round(size / CELLS, 1)}

status_codes = [2, 3, 1, 4]

def wide_row(i):
    return ("anal-1234567890ab", i % 150000, i % 200, "CUSTOMER_NAME_1",
            "비개인정보", "규칙: 순번/정수 값")

def narrow_row(i):
    return (1, i % 150000, i % 200, status_codes[i % 4], "", "")

print("wide text rows:", bench("results/scale/wide.sqlite",
    "CREATE TABLE t (a TEXT,b INTEGER,c INTEGER,d TEXT,e TEXT,f TEXT)", wide_row))
print("narrow coded rows:", bench("results/scale/narrow.sqlite",
    "CREATE TABLE t (a INTEGER,b INTEGER,c INTEGER,d INTEGER,e TEXT,f TEXT)", narrow_row))
