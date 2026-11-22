"""Disk-backed Row/Cell detail store (SQLite).

Every cell hit is written to SQLite as the scan streams, so the detail of a
130,000-row file is never lost when the in-memory cache is smaller than the
number of hits. RAM holds only the current page plus a small write buffer; the
GUI and the exports read the database page by page.

Two-phase verdict
-----------------
While a file streams, a column's support ratio is only a *prefix* of the finished
column, so the final verdict cannot be decided during the scan. The scan writes
the raw evidence with `decision = ''`; once the whole column is known the engine
writes the verdict back into the same record. One record per hit therefore means
no duplicated raw copy and no unbounded in-memory list.

Memory / disk policy
--------------------
* `max_detail_rows`  - how many row verdicts stay in the in-memory cache used by
  the GUI overview. It does not limit what is written to disk.
* `max_detail_cells` - how many cell records one GUI page may render.
* Cell and row detail go to SQLite (WAL), one row per cell hit and one row per
  verdict. Values are masked before they are written, so the database holds no
  raw personal data. `detail_rows_stored` / `detail_cells_stored` report the
  totals and `detail_store_bytes` the on-disk size.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable

CELL_FIELDS = (
    "row", "column_index", "column", "type_key", "display_name", "decision", "confidence",
    "masked_value", "evidence", "needs_review", "column_support", "name_hint",
    "cells_checked", "confirmed", "masked", "sensitivity", "priority",
)

ROW_FIELDS = (
    "row", "has_pii", "status", "confirmed_pii", "possible_pii", "column_names",
    "column_indexes", "cell_count", "basis", "confidence", "risk_level",
    "needs_review", "ai_review", "cells_checked",
)

ROW_SELECT = (
    "SELECT row, has_pii, status, confirmed_pii, possible_pii, column_names, "
    "column_indexes, cell_count, basis, confidence, risk_level, needs_review, "
    "ai_review, cells_checked FROM row_detail"
)

CELL_SELECT = (
    "SELECT row, column_index, column, type_key, display_name, decision, confidence, "
    "masked_value, evidence, needs_review, column_support, name_hint, cells_checked, "
    "confirmed, masked, sensitivity, priority FROM cell_detail WHERE decision <> ''"
)

CELL_SELECT_RANGE = (
    "SELECT row, column_index, column, type_key, display_name, decision, confidence, "
    "masked_value, evidence, needs_review, column_support, name_hint, cells_checked, "
    "confirmed, masked, sensitivity, priority FROM cell_detail "
    "WHERE decision <> '' AND row >= ? AND row <= ? ORDER BY row, column_index"
)

CELL_WITH_ROW = (
    "SELECT c.row, c.column_index, c.column, c.type_key, c.display_name, c.decision, "
    "c.confidence, c.masked_value, c.evidence, c.needs_review, c.column_support, "
    "c.name_hint, c.cells_checked, c.confirmed, c.masked, c.sensitivity, c.priority, "
    "r.status, r.confirmed_pii, r.possible_pii, r.basis, r.risk_level, r.ai_review "
    "FROM cell_detail c JOIN row_detail r ON r.row = c.row "
    "WHERE c.decision <> '' ORDER BY c.row, c.column_index"
)


class DetailStore:
    """Streaming SQLite store for cell evidence and row verdicts."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS cell_detail ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, row INTEGER, column_index INTEGER, "
            "column TEXT, type_key TEXT, display_name TEXT, raw_confidence REAL, "
            "masked_value TEXT, evidence TEXT, confirmed INTEGER, masked INTEGER, "
            "sensitivity REAL, priority INTEGER, name_hint REAL, cells_checked INTEGER, "
            "decision TEXT DEFAULT '', confidence REAL DEFAULT 0, "
            "needs_review INTEGER DEFAULT 0, column_support REAL DEFAULT 0)"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS row_detail ("
            "row INTEGER PRIMARY KEY, has_pii INTEGER, status TEXT, confirmed_pii TEXT, "
            "possible_pii TEXT, column_names TEXT, column_indexes TEXT, cell_count INTEGER, "
            "basis TEXT, confidence REAL, risk_level TEXT, needs_review INTEGER, "
            "ai_review TEXT, cells_checked INTEGER)"
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_cell_row ON cell_detail(row)")
        self.conn.commit()
        self._buffer: list[tuple] = []
        self._buffer_limit = 1000
        self._verdict_buffer: list[tuple] = []
        self.raw_cell_count = 0
        self.row_count = 0
        self.cell_count = 0

    # ---------------------------------------------------------------- write side
    def add_raw_cell(self, row: int, column_index: int, column: str, match, masked: str,
                     name_hint: float, cells_checked: int) -> None:
        self._buffer.append((
            row, column_index, column, match.type_key, match.display_name,
            match.confidence, masked, match.evidence, int(match.confirmed),
            int(match.masked), match.sensitivity, match.priority, round(name_hint, 3),
            cells_checked,
        ))
        self.raw_cell_count += 1
        if len(self._buffer) >= self._buffer_limit:
            self.flush()

    def flush(self) -> None:
        if self._buffer:
            self.conn.executemany(
                "INSERT INTO cell_detail (row, column_index, column, type_key, display_name, "
                "raw_confidence, masked_value, evidence, confirmed, masked, sensitivity, "
                "priority, name_hint, cells_checked) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                self._buffer,
            )
            self._buffer.clear()
            self.conn.commit()

    def set_verdicts(self, updates: list[tuple[str, float, int, float, int]]) -> None:
        """Write the final verdict of each scanned cell back into the same record.

        `updates` is [(decision, confidence, needs_review, column_support, id), ...].
        Ignored cells keep decision = '' so they stay available for diagnostics but
        are never reported as detections.
        """
        if updates:
            self.conn.executemany(
                "UPDATE cell_detail SET decision = ?, confidence = ?, needs_review = ?, "
                "column_support = ? WHERE id = ?",
                updates,
            )
            self.conn.commit()
            self.cell_count += sum(1 for decision, *_ in updates if decision)

    def write_row_detail(self, record) -> None:
        self.conn.execute(
            "INSERT INTO row_detail (row, has_pii, status, confirmed_pii, possible_pii, "
            "column_names, column_indexes, cell_count, basis, confidence, risk_level, "
            "needs_review, ai_review, cells_checked) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (record.row, int(record.has_pii), record.status, json.dumps(record.confirmed_pii),
             json.dumps(record.possible_pii), json.dumps(record.column_names),
             json.dumps(record.column_indexes), record.cell_count, record.basis,
             record.confidence, record.risk_level, int(record.needs_review),
             json.dumps(record.ai_review), record.cells_checked),
        )
        self.row_count += 1

    def update_ai_review(self, row: int, verdict: dict) -> None:
        self.conn.execute("UPDATE row_detail SET ai_review = ? WHERE row = ?",
                         (json.dumps(verdict, ensure_ascii=False), row))
        self.conn.commit()

    @staticmethod
    def _row_dict(raw: tuple) -> dict:
        record = dict(zip(ROW_FIELDS, raw))
        for key in ("confirmed_pii", "possible_pii", "column_names", "column_indexes", "ai_review"):
            record[key] = json.loads(record[key] or "[]") if key != "ai_review" else json.loads(record[key] or "{}")
        record["has_pii"] = bool(record["has_pii"])
        record["needs_review"] = bool(record["needs_review"])
        return record

    # ---------------------------------------------------------------- read side
    def iter_raw_cells(self) -> Iterable[dict]:
        """Every cell the rule engine matched, oldest first, verdict included."""
        self.flush()
        for raw in self.conn.execute(
            "SELECT id, row, column_index, column, type_key, display_name, raw_confidence, "
            "masked_value, evidence, confirmed, masked, sensitivity, priority, name_hint, "
            "cells_checked, decision, confidence, needs_review, column_support "
            "FROM cell_detail ORDER BY row, column_index"
        ):
            yield {
                "id": raw[0], "row": raw[1], "column_index": raw[2], "column": raw[3],
                "type_key": raw[4], "display_name": raw[5], "confidence": raw[6],
                "masked_value": raw[7], "evidence": raw[8], "confirmed": bool(raw[9]),
                "masked": bool(raw[10]), "sensitivity": raw[11], "priority": raw[12],
                "name_hint": raw[13], "cells_checked": raw[14],
                "decision": raw[15], "final_confidence": raw[16],
                "needs_review": bool(raw[17]), "column_support": raw[18],
            }

    def iter_cells(self, offset: int = 0, limit: int | None = None) -> Iterable[dict]:
        query = CELL_SELECT
        params: list = []
        if limit is not None:
            query += " ORDER BY row, column_index LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        for raw in self.conn.execute(query, params):
            yield dict(zip(CELL_FIELDS, raw))

    def cells_between_rows(self, first_row: int, last_row: int) -> Iterable[dict]:
        """Cell detail for a row range - used by the paginated GUI and Row AI context."""
        for raw in self.conn.execute(CELL_SELECT_RANGE, (first_row, last_row)):
            yield dict(zip(CELL_FIELDS, raw))

    def iter_rows(self, offset: int = 0, limit: int | None = None) -> Iterable[dict]:
        query = ROW_SELECT
        params: list = []
        if limit is not None:
            query += " ORDER BY row LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        for raw in self.conn.execute(query, params):
            yield self._row_dict(raw)

    def iter_cells_with_row_context(self) -> Iterable[dict]:
        """Cell hits joined with the verdict of the row they belong to."""
        for raw in self.conn.execute(CELL_WITH_ROW):
            yield {
                "row": raw[0], "column_index": raw[1], "column": raw[2], "type_key": raw[3],
                "display_name": raw[4], "decision": raw[5], "confidence": raw[6],
                "masked_value": raw[7], "evidence": raw[8], "needs_review": bool(raw[9]),
                "column_support": raw[10], "name_hint": raw[11], "cells_checked": raw[12],
                "confirmed": bool(raw[13]), "masked": bool(raw[14]), "sensitivity": raw[15],
                "priority": raw[16], "row_status": raw[17],
                "row_confirmed": json.loads(raw[18] or "[]"),
                "row_possible": json.loads(raw[19] or "[]"),
                "row_basis": raw[20], "row_risk": raw[21],
                "row_ai": json.loads(raw[22] or "{}"),
            }

    def review_row_count(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM row_detail WHERE needs_review = 1").fetchone()[0]

    def review_rows(self, limit: int | None = None) -> list[dict]:
        """Review-worthy rows across the whole file.

        `limit` selects a representative stride sample rather than the first
        `limit` rows, so a truncated detail cache cannot bias which rows the AI
        examines. Rows are streamed, so only the sampled rows are held in memory.
        """
        total = self.review_row_count()
        stride = 1 if limit is None or total <= limit else max(1, total // limit)
        out: list[dict] = []
        index = 0
        for raw in self.conn.execute(ROW_SELECT + " WHERE needs_review = 1 ORDER BY row"):
            if index % stride == 0:
                out.append(self._row_dict(raw))
                if limit is not None and len(out) >= limit:
                    break
            index += 1
        return out

    def stats(self) -> dict:
        self.flush()
        return {
            "detail_rows_stored": self.row_count,
            "detail_cells_stored": self.cell_count,
            "raw_cell_hits": self.raw_cell_count,
            "detail_store_bytes": self.path.stat().st_size if self.path.exists() else 0,
            "detail_store_path": str(self.path),
        }

    def close(self) -> None:
        self.flush()
        self.conn.close()
