"""Compare per-cell SQLite layouts: one row per cell vs a per-row status blob."""
from __future__ import annotations
import sqlite3, time, os, random
from pathlib import Path

ROWS, COLS = 150_000, 200
rng = random.Random(11)

def layout_rows(path):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE cell (analysis TEXT, row INTEGER, col INTEGER, status INTEGER, conf REAL, evid TEXT)")
    conn.execute("CREATE INDEX idx_cell ON cell(analysis, row)")
    t = time.perf_counter()
    batch = []
    for r in range(ROWS):
        for c in range(COLS):
            batch.append(("anal-1234567890ab", r + 1, c, rng.randint(0, 5), 0.5, ""))
            if len(batch) >= 5000:
                conn.executemany("INSERT INTO cell VALUES (?,?,?,?,?,?)", batch)
                batch = []
    conn.executemany("INSERT INTO cell VALUES (?,?,?,?,?,?)", batch)
    conn.commit()
    print("per-row layout:", round(time.perf_counter() - t, 2), "s",
          round(os.path.getsize(path) / 1024 / 1024, 1), "MB")
    conn.close()

def layout_blob(path):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE cellblob (analysis TEXT, row INTEGER, status BLOB)")
    conn.execute("CREATE INDEX idx_blob ON cellblob(analysis, row)")
    t = time.perf_counter()
    batch = []
    for r in range(ROWS):
        batch.append(("anal-1234567890ab", r + 1, bytes(rng.randint(0, 5) for _ in range(COLS))))
        if len(batch) >= 2000:
            conn.executemany("INSERT INTO cellblob VALUES (?,?,?)", batch)
            batch = []
    conn.executemany("INSERT INTO cellblob VALUES (?,?,?)", batch)
    conn.commit()
    cells = ROWS * COLS
    print("blob layout:", round(time.perf_counter() - t, 2), "s",
          round(os.path.getsize(path) / 1024 / 1024, 1), "MB",
          "->", round(os.path.getsize(path) / cells, 1), "B/cell")
    conn.close()

Path("results/scale").mkdir(exist_ok=True)
layout_rows("results/scale/layout_rows.sqlite")
layout_blob("results/scale/layout_blob.sqlite")
