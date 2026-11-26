"""Disk-backed Row/Cell detail store (SQLite), Layout D.

The scan streams millions of cells, so one SQLite *row per cell* is not a viable
ledger: a 150,000 x 200 file measured 10.3 GB with the old full-text
`cell_verdict` table. Every cell still gets a verdict - it is just stored in two
parts:

* `row_status` - one row per source row, holding one status **byte per column**.
  Empty cells, KEY-excluded cells and rule-cleared cells need nothing else, so
  they cost 1 byte each (the clearing reason is the byte itself).
* `cell_info` - one row per cell that has something to say: a rule match, a rule
  that could not decide (`미평가`), or an AI judgement. Those are the cells a
  reviewer or the model must look at, and they carry the masked value, the
  evidence and the HMAC fingerprint.

Everything a report needs for a derived cell (빈 셀 / KEY 제외 / 비개인정보) is
reconstructed from the status byte, so the Cell ledger is complete without
storing a record per cell. `verify_completeness()` checks the arithmetic:
`derived + cell_info == logical cells`.

Identity
--------
A record is addressed by `analysis_id + table_id + row + column_index`. `row` is
the physical row number and is always unique; the KEY fingerprint lives in a
separate column (`row_status.key_fingerprint`) so rows that share a KEY value -
or have no KEY at all - are never merged.

Privacy
-------
Raw personal values are never written. Only `mask_value()` output and HMAC
fingerprints are stored. The HMAC salt is generated per run and is not persisted
anywhere, so fingerprints cannot be reversed or compared across runs.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
from pathlib import Path
from typing import Iterable

from app.core.column_stats import Hit
from app.core.cell_verdict import (
    CODE_CLEARED_BASE, CODE_DETAIL, CODE_EMPTY, CODE_KEY, NON_PERSONAL_LABELS,
    STATUS_EMPTY, STATUS_KEY, STATUS_NON, STATUS_PII, STATUS_REVIEW, STATUS_UNRATED,
    code_to_evidence, code_to_rule_verdict, code_to_status, status_to_code,
)

CELL_FIELDS = (
    "row", "column_index", "column", "type_key", "display_name", "decision", "confidence",
    "masked_value", "evidence", "needs_review", "column_support", "name_hint",
    "cells_checked", "confirmed", "masked", "sensitivity", "priority",
)

VERDICT_FIELDS = (
    "row", "column_index", "column", "cell_status", "privacy_types", "rule_verdict",
    "ai_verdict", "final_verdict", "confidence", "decision_method", "needs_review",
    "masked_value", "evidence",
)

ROW_FIELDS = (
    "row", "has_pii", "status", "confirmed_pii", "possible_pii", "column_names",
    "column_indexes", "cell_count", "basis", "confidence", "risk_level",
    "needs_review", "ai_review", "cells_checked",
)

ROW_SELECT = (
    "SELECT row, has_pii, status, confirmed_pii, possible_pii, column_names, "
    "column_indexes, cell_count, basis, confidence, risk_level, needs_review, "
    "ai_review, cells_checked FROM row_detail WHERE analysis_id = ?"
)

# Every cell with evidence (rule match, undecided cell or AI judgement).
INFO_SELECT = (
    "SELECT row, column_index, column, cell_status, privacy_types, rule_verdict, "
    "ai_verdict, final_verdict, confidence, decision_method, needs_review, masked_value, "
    "evidence FROM cell_info WHERE analysis_id = ?"
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
    "WHERE analysis_id = ? AND decision <> '' AND row >= ? AND row <= ? "
    "ORDER BY row, column_index"
)

CELL_WITH_ROW = (
    "SELECT c.row, c.column_index, c.column, c.type_key, c.display_name, c.decision, "
    "c.confidence, c.masked_value, c.evidence, c.needs_review, c.column_support, "
    "c.name_hint, c.cells_checked, c.confirmed, c.masked, c.sensitivity, c.priority, "
    "r.status, r.confirmed_pii, r.possible_pii, r.basis, r.risk_level, r.ai_review "
    "FROM cell_detail c JOIN row_detail r ON r.analysis_id = c.analysis_id AND r.row = c.row "
    "WHERE c.analysis_id = ? AND c.decision <> '' ORDER BY c.row, c.column_index"
)

# Statuses that live in `cell_info`; the rest are derived from the status byte.
INFO_STATUSES = (STATUS_PII, STATUS_REVIEW, STATUS_UNRATED)
DERIVED_STATUSES = (STATUS_EMPTY, STATUS_KEY, STATUS_NON)


class DetailStore:
    """Streaming SQLite store for cell verdicts, row summaries and PII groups."""

    def __init__(self, path: str | Path, analysis_id: str = "", kind: str = "table",
                 resume: bool = False):
        self.path = Path(path)
        self.analysis_id = analysis_id or self.path.stem
        self.kind = kind  # "table" (Function A) | "metadata" (Function B)
        self.temp_dir = None  # set by the caller when the store lives in a throwaway dir
        self.cleaned = False
        self._closed = False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # HMAC fingerprints are scoped to one run: the salt is never persisted.
        self._salt = secrets.token_bytes(16)
        # The GUI reads the store from the widget thread while the analysis worker
        # writes it from another thread, so the connection is shared and every
        # statement is serialised with a lock.
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        # A SIGTERM delivered while the writer thread sits inside `_exec_many`
        # runs the cleanup handler on that same thread. With a plain Lock the
        # handler's `close() -> flush() -> _exec_many` would wait for the lock
        # it already owns and hang forever (measured: the lifecycle test timed
        # out at 300 s). An RLock lets the same thread finish its own teardown.
        self._lock = threading.RLock()
        self._exec("PRAGMA journal_mode=WAL")
        self._exec("PRAGMA synchronous=NORMAL")
        # Bulk analyse writes hundreds of megabytes in one transaction stream.
        # Letting the WAL grow and checkpointing once at close avoids rewriting
        # the main database after every bulk commit.
        self._exec("PRAGMA wal_autocheckpoint=1000000")
        self._exec(
            "CREATE TABLE IF NOT EXISTS runs (analysis_id TEXT PRIMARY KEY, kind TEXT NOT NULL, "
            "file TEXT NOT NULL, table_id INTEGER NOT NULL, db_schema TEXT NOT NULL DEFAULT '', "
            "db_table TEXT NOT NULL DEFAULT '', db_type TEXT NOT NULL DEFAULT '', "
            "logical_cells INTEGER NOT NULL DEFAULT 0, key_cells INTEGER NOT NULL DEFAULT 0, "
            "last_row INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'running', "
            "created_at TEXT NOT NULL DEFAULT '', "
            "max_rows INTEGER NOT NULL DEFAULT 0, max_cols INTEGER NOT NULL DEFAULT 0, "
            "column_count INTEGER NOT NULL DEFAULT 0)"
        )
        # Stores created before the analysis-range feature have no scope columns;
        # add them so an old checkpoint can still be read and compared.
        for column in ("max_rows", "max_cols", "column_count"):
            try:
                self._exec(f"ALTER TABLE runs ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0")
            except Exception:
                pass  # the column already exists
        # One status byte per column of one source row.
        self._exec(
            "CREATE TABLE IF NOT EXISTS row_status ("
            "analysis_id TEXT NOT NULL, table_id INTEGER NOT NULL, row INTEGER NOT NULL, "
            "width INTEGER NOT NULL, status BLOB NOT NULL, key_fingerprint TEXT NOT NULL DEFAULT '', "
            "row_key TEXT NOT NULL DEFAULT '', PRIMARY KEY (analysis_id, table_id, row))"
        )
        self._exec(
            "CREATE INDEX IF NOT EXISTS idx_row_status_row "
            "ON row_status(analysis_id, table_id, row)"
        )
        # Per-cell detail: only cells that carry evidence get a record here.
        self._exec(
            "CREATE TABLE IF NOT EXISTS cell_info ("
            "analysis_id TEXT NOT NULL, table_id INTEGER NOT NULL, row INTEGER NOT NULL, "
            "column_index INTEGER NOT NULL, column TEXT NOT NULL, cell_status TEXT NOT NULL, "
            "privacy_types TEXT NOT NULL DEFAULT '', rule_verdict TEXT NOT NULL DEFAULT '', "
            "ai_verdict TEXT NOT NULL DEFAULT '', final_verdict TEXT NOT NULL DEFAULT '', "
            "confidence REAL NOT NULL DEFAULT 0, decision_method TEXT NOT NULL DEFAULT '', "
            "needs_review INTEGER NOT NULL DEFAULT 0, masked_value TEXT NOT NULL DEFAULT '', "
            "evidence TEXT NOT NULL DEFAULT '', fingerprint TEXT NOT NULL DEFAULT '', "
            "PRIMARY KEY (analysis_id, table_id, row, column_index))"
        )
        self._exec(
            "CREATE INDEX IF NOT EXISTS idx_info_status "
            "ON cell_info(analysis_id, table_id, cell_status, row, column_index)"
        )
        self._exec(
            "CREATE INDEX IF NOT EXISTS idx_info_fingerprint "
            "ON cell_info(analysis_id, fingerprint)"
        )
        self._exec(
            "CREATE INDEX IF NOT EXISTS idx_info_row "
            "ON cell_info(analysis_id, row, cell_status)"
        )
        # One record per detector match (the hit report and the row context).
        self._exec(
            "CREATE TABLE IF NOT EXISTS cell_detail ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, analysis_id TEXT NOT NULL, "
            "table_id INTEGER NOT NULL DEFAULT 0, row INTEGER, column_index INTEGER, "
            "column TEXT, type_key TEXT, display_name TEXT, raw_confidence REAL, "
            "masked_value TEXT, evidence TEXT, confirmed INTEGER, masked INTEGER, "
            "sensitivity REAL, priority INTEGER, name_hint REAL, cells_checked INTEGER, "
            "decision TEXT DEFAULT '', confidence REAL DEFAULT 0, "
            "needs_review INTEGER DEFAULT 0, column_support REAL DEFAULT 0, "
            "fingerprint TEXT NOT NULL DEFAULT '')"
        )
        self._exec(
            "CREATE INDEX IF NOT EXISTS idx_cell_analysis ON cell_detail(analysis_id, row)"
        )
        self._exec(
            "CREATE TABLE IF NOT EXISTS row_verdict ("
            "analysis_id TEXT NOT NULL, table_id INTEGER NOT NULL, row INTEGER NOT NULL, "
            "total_cells INTEGER NOT NULL, empty_cells INTEGER NOT NULL, key_cells INTEGER NOT NULL, "
            "pii_cells INTEGER NOT NULL, review_cells INTEGER NOT NULL, non_pii_cells INTEGER NOT NULL, "
            "unrated_cells INTEGER NOT NULL, status TEXT NOT NULL, has_pii INTEGER NOT NULL, "
            "PRIMARY KEY (analysis_id, table_id, row))"
        )
        self._exec(
            "CREATE TABLE IF NOT EXISTS row_detail ("
            "analysis_id TEXT NOT NULL, table_id INTEGER NOT NULL DEFAULT 0, row INTEGER, "
            "has_pii INTEGER, status TEXT, confirmed_pii TEXT, possible_pii TEXT, "
            "column_names TEXT, column_indexes TEXT, cell_count INTEGER, "
            "basis TEXT, confidence REAL, risk_level TEXT, needs_review INTEGER, "
            "ai_review TEXT, cells_checked INTEGER)"
        )
        self._exec(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_row_analysis ON row_detail(analysis_id, row)"
        )
        # Column names by index, so a derived (byte-only) cell can still be
        # reported with its real column header, including EXTRA_COL_n columns.
        self._exec(
            "CREATE TABLE IF NOT EXISTS layout_columns ("
            "analysis_id TEXT NOT NULL, table_id INTEGER NOT NULL, column_index INTEGER NOT NULL, "
            "column TEXT NOT NULL, PRIMARY KEY (analysis_id, table_id, column_index))"
        )
        # Groups of cells with the *same* personal-data fingerprint.
        self._exec(
            "CREATE TABLE IF NOT EXISTS pii_group ("
            "analysis_id TEXT NOT NULL, table_id INTEGER NOT NULL, fingerprint TEXT NOT NULL, "
            "pii_type TEXT NOT NULL, occurrences INTEGER NOT NULL, distinct_rows INTEGER NOT NULL, "
            "distinct_records INTEGER NOT NULL, columns TEXT NOT NULL DEFAULT '', "
            "key_samples TEXT NOT NULL DEFAULT '', first_row INTEGER, last_row INTEGER, "
            "PRIMARY KEY (analysis_id, table_id, fingerprint, pii_type))"
        )
        if kind == "metadata":
            self._exec(
                "CREATE TABLE IF NOT EXISTS meta_records (analysis_id TEXT NOT NULL, "
                "table_name TEXT, table_comment TEXT, column_name TEXT, column_comment TEXT, "
                "data_type TEXT, length INTEGER, nullable INTEGER, primary_key INTEGER, "
                "foreign_key INTEGER, schema_name TEXT, db_name TEXT, PRIMARY KEY (analysis_id, table_name, column_name))"
            )
            self._exec(
                "CREATE TABLE IF NOT EXISTS column_verdicts (analysis_id TEXT NOT NULL, "
                "table_name TEXT NOT NULL, column_name TEXT NOT NULL, verdict TEXT NOT NULL, "
                "type_candidates TEXT NOT NULL DEFAULT '', confidence REAL NOT NULL DEFAULT 0, "
                "method TEXT NOT NULL DEFAULT '', rationale TEXT NOT NULL DEFAULT '', "
                "needs_confirmation INTEGER NOT NULL DEFAULT 0, "
                "PRIMARY KEY (analysis_id, table_name, column_name))"
            )
            self._exec(
                "CREATE TABLE IF NOT EXISTS table_verdicts (analysis_id TEXT NOT NULL, "
                "table_name TEXT NOT NULL, relevance REAL NOT NULL DEFAULT 0, "
                "pii_column_count INTEGER NOT NULL DEFAULT 0, candidate_types TEXT NOT NULL DEFAULT '', "
                "status TEXT NOT NULL DEFAULT '', rationale TEXT NOT NULL DEFAULT '', "
                "column_count INTEGER NOT NULL DEFAULT 0, "
                "PRIMARY KEY (analysis_id, table_name))"
            )
            self._exec(
                "CREATE INDEX IF NOT EXISTS idx_column_verdict_status "
                "ON column_verdicts(analysis_id, verdict, table_name)"
            )
        # Re-analysing the same file into a shared store replaces that file's own
        # records instead of colliding with a previous run - unless the run is a
        # resume, in which case the checkpoint rows are exactly what must survive.
        if not resume:
            for table in ("cell_detail", "cell_info", "row_status", "row_verdict", "row_detail", "pii_group"):
                self._exec(f"DELETE FROM {table} WHERE analysis_id = ?", (self.analysis_id,))
        self._commit()

        self._buffer: list[tuple] = []
        self._buffer_limit = 1000
        self._info_buffer: list[tuple] = []
        self._info_limit = 5000
        self._row_buffer: list[tuple] = []
        self._row_limit = 2000
        self._row_detail_buffer: list[tuple] = []
        self._row_detail_limit = 1000
        self._pending_commits = 0
        self.raw_cell_count = 0
        self.rows_with_detail = 0
        self.decided_cells = 0
        self.verdict_records = 0
        self.key_cells = 0
        self.empty_cells = 0
        self.logical_cells = 0
        self.table_id = 0
        self._current_row: int | None = None
        self._codes = bytearray()
        self._breakdown: dict[str, int] | None = None

    def _exec(self, sql: str, params: tuple = ()):
        with self._lock:
            return self.conn.execute(sql, params)

    def _exec_many(self, sql: str, rows) -> None:
        with self._lock:
            self.conn.executemany(sql, rows)

    def _commit(self) -> None:
        with self._lock:
            self.conn.commit()

    # ---------------------------------------------------------------- identity
    def register_run(self, file: str, schema: str = "", table: str = "", db_type: str = "",
                     resume: bool = False, max_rows: int = 0, max_cols: int = 0,
                     column_count: int = 0) -> int:
        """Register this analysis and return its `table_id` (row identity namespace).

        A resumed run keeps its checkpoint: `last_row` is only reset when the
        analysis starts from the beginning. The requested analysis range is stored
        so a later `--resume` can check it against the checkpoint.
        """
        table_id = int.from_bytes(
            hashlib.sha1(f"{schema}|{table}|{file}".encode("utf-8")).digest()[:4], "big"
        )
        self.table_id = table_id
        if resume:
            self._exec(
                "INSERT INTO runs (analysis_id, kind, file, table_id, db_schema, db_table, db_type, "
                "max_rows, max_cols, column_count) "
                "VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(analysis_id) DO UPDATE SET "
                "kind = excluded.kind, file = excluded.file, table_id = excluded.table_id, "
                "db_schema = excluded.db_schema, db_table = excluded.db_table, "
                "db_type = excluded.db_type, max_rows = excluded.max_rows, "
                "max_cols = excluded.max_cols, column_count = excluded.column_count",
                (self.analysis_id, self.kind, file, table_id, schema, table, db_type,
                 max_rows, max_cols, column_count),
            )
        else:
            self._exec(
                "INSERT OR REPLACE INTO runs (analysis_id, kind, file, table_id, db_schema, db_table, "
                "db_type, max_rows, max_cols, column_count) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (self.analysis_id, self.kind, file, table_id, schema, table, db_type,
                 max_rows, max_cols, column_count),
            )
        self._commit()
        return table_id

    def checkpoint_scope(self) -> dict | None:
        """The range recorded by a previous run of this file, or None if absent."""
        row = self._exec(
            "SELECT file, db_schema, db_table, last_row, max_rows, max_cols, column_count "
            "FROM runs WHERE analysis_id = ?", (self.analysis_id,)
        ).fetchone()
        if row is None:
            return None
        return {"file": row[0], "db_schema": row[1], "db_table": row[2], "last_row": int(row[3]),
                "max_rows": int(row[4]), "max_cols": int(row[5]), "column_count": int(row[6])}

    def reset_checkpoint(self, max_rows: int = 0, max_cols: int = 0, column_count: int = 0) -> None:
        """Discard a checkpoint that cannot be continued (changed analysis range)."""
        self._exec("UPDATE runs SET last_row = 0, status = 'running', max_rows = ?, max_cols = ?, "
                   "column_count = ? WHERE analysis_id = ?",
                   (max_rows, max_cols, column_count, self.analysis_id))
        self._commit()

    def register_columns(self, names: Iterable[str]) -> None:
        """Record the column layout (headers plus any EXTRA_COL_n) for this run."""
        self._exec_many(
            "INSERT OR REPLACE INTO layout_columns (analysis_id, table_id, column_index, column) "
            "VALUES (?,?,?,?)",
            [(self.analysis_id, self.table_id, index, name) for index, name in enumerate(names)],
        )

    def seed_column_stats(self, stats) -> dict:
        """Restore per-column counters for rows already decided in an earlier run.

        Without this a resumed scan would report column-level statistics for the
        rows it processed only this session, which would under-count the analysis.
        Length buckets and distinct-value sketches are not restorable and stay
        scoped to the resumed portion; the cell and row ledgers are exact.
        """
        self.flush()
        for (blob,) in self._exec(
            "SELECT status FROM row_status WHERE analysis_id = ?", (self.analysis_id,)
        ):
            codes = bytearray(blob)
            for index, code in enumerate(codes):
                if index >= len(stats):
                    continue
                column = stats[index]
                column.rows += 1
                if code == CODE_EMPTY:
                    column.null_count += 1
                else:
                    column.non_empty += 1
        for (index, name, type_key, display_name, count, confidence_sum, max_confidence,
             masked, confirmed, evidence, sensitivity, priority) in self._exec(
            """SELECT column_index, column, type_key, display_name, COUNT(*),
                       SUM(raw_confidence), MAX(raw_confidence), SUM(masked),
                       SUM(confirmed), MIN(evidence), MAX(sensitivity), MAX(priority)
                FROM cell_detail WHERE analysis_id = ? AND decision <> ''
                GROUP BY column_index, column, type_key, display_name""",
            (self.analysis_id,),
        ):
            if index >= len(stats):
                continue
            column = stats[index]
            hit = column.hits.get(type_key)
            if hit is None:
                hit = Hit(type_key=type_key, display_name=display_name, evidence=evidence,
                          confirmed=bool(confirmed), sensitivity=sensitivity, priority=priority)
                column.hits[type_key] = hit
            hit.count += count
            hit.masked_count += masked
            hit.confirmed_count += confirmed
            hit.confidence_sum += confidence_sum
            hit.max_confidence = max(hit.max_confidence, max_confidence)
            hit.confirmed = hit.confirmed and bool(confirmed)
            if not hit.evidence:
                hit.evidence = evidence
            rows = column.hit_rows.setdefault(type_key, [])
            if len(rows) < column.max_hit_rows:
                for row_number, masked_value, confidence in self._exec(
                    "SELECT row, masked_value, confidence FROM cell_detail "
                    "WHERE analysis_id = ? AND column_index = ? AND type_key = ? "
                    "AND decision <> '' ORDER BY row LIMIT ?",
                    (self.analysis_id, index, type_key, column.max_hit_rows - len(rows)),
                ):
                    rows.append({"row": row_number, "value": masked_value,
                                 "confidence": round(confidence, 3)})
        return {"columns_seeded": len(stats),
                "rows_restored": sum(column.rows for column in stats)}

    def resume_row(self) -> int:
        """Last row already analysed for this analysis_id (0 = start from scratch)."""
        row = self._exec(
            "SELECT last_row FROM runs WHERE analysis_id = ?", (self.analysis_id,)
        ).fetchone()
        return int(row[0]) if row else 0

    def mark_last_row(self, row: int, status: str = "running") -> None:
        self._exec(
            "UPDATE runs SET last_row = ?, status = ?, logical_cells = ?, key_cells = ? "
            "WHERE analysis_id = ?",
            (row, status, self.logical_cells, self.key_cells, self.analysis_id),
        )
        self._commit()

    def fingerprint(self, value: str) -> str:
        """HMAC fingerprint of a normalised value - never the value itself."""
        from app.core.cell_verdict import normalise_value

        normalised = normalise_value(value)
        if not normalised:
            return ""
        return hashlib.sha256(self._salt + normalised.encode("utf-8")).hexdigest()[:16]

    # ---------------------------------------------------------------- write side
    def add_cell_verdict(self, row: int, column_index: int, column: str, status: str,
                         types: Iterable[str] = (), rule_verdict: str = "",
                         masked_value: str = "", confidence: float = 0.0,
                         decision_method: str = "규칙", needs_review: bool = False,
                         evidence: str = "", fingerprint: str = "") -> None:
        """Record the verdict of one cell.

        Cells that need no explanation (empty, KEY-excluded, positively cleared by
        a rule) are stored as a single status byte in `row_status`; every other
        cell gets a `cell_info` record so its evidence is never lost.
        """
        if self._current_row != row:
            self._flush_current_row()
            self._current_row = row
            self._codes = bytearray()
        code = status_to_code(status, rule_verdict)
        if len(self._codes) <= column_index:
            self._codes.extend(b"\0" * (column_index + 1 - len(self._codes)))
        self._codes[column_index] = code
        self.verdict_records += 1
        if status in INFO_STATUSES:
            self._info_buffer.append((
                self.analysis_id, self.table_id, row, column_index, column, status,
                ",".join(types), rule_verdict, "", "", round(confidence, 3),
                decision_method, int(needs_review), masked_value, evidence, fingerprint,
            ))
            if len(self._info_buffer) >= self._info_limit:
                self.flush_cell_info()
        if status == STATUS_EMPTY:
            self.empty_cells += 1
        elif status == STATUS_KEY:
            self.key_cells += 1
        self.logical_cells += 1

    def set_row_key(self, row: int, fingerprint: str, row_key: str) -> None:
        """Attach the KEY fingerprint (identity of the record) to a source row."""
        self._exec(
            "INSERT INTO row_status (analysis_id, table_id, row, width, status, key_fingerprint, row_key) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(analysis_id, table_id, row) "
            "DO UPDATE SET key_fingerprint = excluded.key_fingerprint, row_key = excluded.row_key",
            (self.analysis_id, self.table_id, row, 0, b"", fingerprint, row_key),
        )

    def close_row(self, key_fingerprint: str = "", row_key: str = "") -> None:
        """Finish one source row: write its status bytes plus its KEY fingerprint."""
        if self._current_row is None:
            return
        self._row_buffer.append((self.analysis_id, self.table_id, self._current_row,
                                 len(self._codes), bytes(self._codes), key_fingerprint, row_key))
        self._current_row = None
        self._codes = bytearray()
        if len(self._row_buffer) >= self._row_limit:
            self._flush_rows()

    def _flush_current_row(self) -> None:
        if self._current_row is None or not self._codes:
            return
        self._row_buffer.append((self.analysis_id, self.table_id, self._current_row,
                                 len(self._codes), bytes(self._codes), "", ""))
        if len(self._row_buffer) >= self._row_limit:
            self._flush_rows()

    def _flush_rows(self) -> None:
        if not self._row_buffer:
            return
        self._exec_many(
            "INSERT OR REPLACE INTO row_status (analysis_id, table_id, row, width, status, "
            "key_fingerprint, row_key) VALUES (?,?,?,?,?,?,?)",
            self._row_buffer,
        )
        self._row_buffer.clear()
        self._pending_commits += 1

    def flush_cell_info(self) -> None:
        if self._info_buffer:
            self._exec_many(
                "INSERT OR REPLACE INTO cell_info (analysis_id, table_id, row, column_index, column, "
                "cell_status, privacy_types, rule_verdict, ai_verdict, final_verdict, confidence, "
                "decision_method, needs_review, masked_value, evidence, fingerprint) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                self._info_buffer,
            )
            self._info_buffer.clear()
            self._pending_commits += 1

    def flush(self) -> None:
        """Write buffered rows to SQLite without committing.

        Committing after every small buffer flush costs more than the inserts
        themselves (measured: ~2.5 ms per commit, hundreds of commits per file).
        `commit()` is called at batch boundaries instead, so a crash still keeps
        everything scanned so far.
        """
        self.flush_cell_info()
        self._flush_current_row()
        self._flush_rows()
        self._flush_row_detail()
        if self._buffer:
            self._exec_many(
                "INSERT INTO cell_detail (analysis_id, table_id, row, column_index, column, type_key, "
                "display_name, raw_confidence, masked_value, evidence, confirmed, masked, "
                "sensitivity, priority, name_hint, cells_checked, fingerprint) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                self._buffer,
            )
            self._buffer.clear()
            self._pending_commits += 1

    def commit(self) -> None:
        if self._pending_commits:
            self._commit()
            self._pending_commits = 0

    def add_raw_cell(self, row: int, column_index: int, column: str, match, masked: str,
                     name_hint: float, cells_checked: int, fingerprint: str = "") -> None:
        self._buffer.append((
            self.analysis_id, self.table_id, row, column_index, column, match.type_key,
            match.display_name, match.confidence, masked, match.evidence, int(match.confirmed),
            int(match.masked), match.sensitivity, match.priority, round(name_hint, 3),
            cells_checked, fingerprint,
        ))
        self.raw_cell_count += 1
        if len(self._buffer) >= self._buffer_limit:
            self.flush()

    def set_verdicts(self, updates: list[tuple]) -> None:
        if updates:
            self._exec_many(
                "UPDATE cell_detail SET decision = ?, confidence = ?, needs_review = ?, "
                "column_support = ? WHERE id = ?",
                updates,
            )
            self._pending_commits += 1
            self.decided_cells += sum(1 for decision, *_ in updates if decision)

    def merge_cell_rule_verdicts(self, updates: list[tuple]) -> None:
        """Fold each detector verdict into the one record that represents the cell.

        A cell can be matched by several detectors; the engine hands over the
        already-merged answer (strongest status, union of types, highest
        confidence), so this is a plain assignment rather than a per-hit merge.
        `updates` is [(status, types, confidence, needs_review, row, column_index,
                       rule_verdict, evidence), ...].
        """
        if not updates:
            return
        self._exec_many(
            "UPDATE cell_info SET cell_status = ?, privacy_types = ?, confidence = ?, "
            "needs_review = ?, rule_verdict = ?, final_verdict = ?, decision_method = '규칙', "
            "evidence = ? "
            "WHERE analysis_id = ? AND table_id = ? AND row = ? AND column_index = ?",
            [(status, types, round(confidence, 3), int(review), rule_verdict, status, evidence,
              self.analysis_id, self.table_id, row, index)
             for status, types, confidence, review, row, index, rule_verdict, evidence in updates],
        )
        self._pending_commits += 1
        self._breakdown = None

    def set_cell_ai_verdicts(self, updates: list[tuple]) -> None:
        """Attach the AI verdict and the merged final verdict to cells in bulk.

        `updates` is [(ai_verdict, status, confidence, method, needs_review,
                       row, column_index), ...]. A cell can only move *up* in
        certainty from the rule engine's answer; the AI never silently erases a
        rule-confirmed personal-data verdict.
        """
        if not updates:
            return
        self._exec_many(
            "UPDATE cell_info SET ai_verdict = ?, cell_status = ?, final_verdict = ?, "
            "confidence = ?, decision_method = ?, needs_review = ? "
            "WHERE analysis_id = ? AND table_id = ? AND row = ? AND column_index = ?",
            [(ai, status, status, round(conf, 3), method, int(review),
              self.analysis_id, self.table_id, row, index)
             for ai, status, conf, method, review, row, index in updates],
        )
        self._pending_commits += 1
        self._breakdown = None

    def write_row_detail(self, record) -> None:
        """Queue one Row summary; the insert is batched like every other write."""
        self.rows_with_detail += 1
        self._row_detail_buffer.append((
            self.analysis_id, self.table_id, record.row, int(record.has_pii), record.status,
            json.dumps(record.confirmed_pii), json.dumps(record.possible_pii),
            json.dumps(record.column_names), json.dumps(record.column_indexes), record.cell_count,
            record.basis, record.confidence, record.risk_level, int(record.needs_review),
            json.dumps(record.ai_review), record.cells_checked,
        ))
        if len(self._row_detail_buffer) >= self._row_detail_limit:
            self._flush_row_detail()

    def _flush_row_detail(self) -> None:
        if not self._row_detail_buffer:
            return
        self._exec_many(
            "INSERT OR REPLACE INTO row_detail (analysis_id, table_id, row, has_pii, status, "
            "confirmed_pii, "
            "possible_pii, column_names, column_indexes, cell_count, basis, confidence, "
            "risk_level, needs_review, ai_review, cells_checked) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            self._row_detail_buffer,
        )
        self._row_detail_buffer.clear()
        self._pending_commits += 1

    def update_ai_review(self, row: int, verdict: dict) -> None:
        self._exec(
            "UPDATE row_detail SET ai_review = ? WHERE analysis_id = ? AND row = ?",
            (json.dumps(verdict, ensure_ascii=False), self.analysis_id, row),
        )
        self._commit()

    # ---------------------------------------------------------------- aggregates
    def aggregate_row_verdicts(self) -> dict:
        """Derive the Row summary from the Cell ledger (never from a column guess).

        Rows are decoded in windows of 1,000 so RAM stays bounded by the window,
        and the result is written to `row_verdict` for paging.
        """
        self.flush()
        self._exec("DELETE FROM row_verdict WHERE analysis_id = ?", (self.analysis_id,))
        window = 1000
        totals = {status: 0 for status in (STATUS_PII, STATUS_REVIEW, STATUS_NON,
                                         STATUS_UNRATED, STATUS_EMPTY, STATUS_KEY)}
        row_count = 0
        start_row = 1
        while True:
            rows_raw = list(self._exec(
                "SELECT row, status FROM row_status WHERE analysis_id = ? AND row >= ? "
                "ORDER BY row LIMIT ?", (self.analysis_id, start_row, window)))
            if not rows_raw:
                break
            last_row = rows_raw[-1][0]
            detail_counts: dict[int, dict[str, int]] = {}
            for row_number, cell_status, count in self._exec(
                "SELECT row, cell_status, COUNT(*) FROM cell_info "
                "WHERE analysis_id = ? AND row >= ? AND row <= ? GROUP BY row, cell_status",
                (self.analysis_id, start_row, last_row),
            ):
                detail_counts.setdefault(row_number, {})[cell_status] = count
            rows: list[tuple] = []
            for row_number, blob in rows_raw:
                codes = bytearray(blob)
                empty = codes.count(CODE_EMPTY)
                key = codes.count(CODE_KEY)
                detail = codes.count(CODE_DETAIL)
                # Cells that carry a `cell_info` record are counted from that record,
                # so only the byte-only (derived) cells come from the status blob.
                counts = detail_counts.get(row_number, {})
                non_pii = len(codes) - empty - key - detail + counts.get(STATUS_NON, 0)
                pii = counts.get(STATUS_PII, 0)
                review = counts.get(STATUS_REVIEW, 0)
                unrated = counts.get(STATUS_UNRATED, 0)
                totals[STATUS_PII] += pii
                totals[STATUS_REVIEW] += review
                totals[STATUS_NON] += non_pii
                totals[STATUS_UNRATED] += unrated
                totals[STATUS_EMPTY] += empty
                totals[STATUS_KEY] += key
                if pii:
                    row_status = STATUS_PII
                elif review:
                    row_status = STATUS_REVIEW
                elif unrated:
                    row_status = STATUS_UNRATED
                elif key and not non_pii:
                    row_status = STATUS_KEY
                else:
                    row_status = STATUS_NON
                rows.append((self.analysis_id, self.table_id, row_number, len(codes), empty, key,
                             pii, review, non_pii, unrated, row_status, int(pii or review)))
                row_count += 1
            if rows:
                self._exec_many(
                    "INSERT OR REPLACE INTO row_verdict (analysis_id, table_id, row, total_cells, "
                    "empty_cells, key_cells, pii_cells, review_cells, non_pii_cells, unrated_cells, "
                    "status, has_pii) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows,
                )
            start_row = last_row + 1
        self._commit()
        if row_count:
            self._breakdown = dict(totals)
            return dict(totals)
        return self.status_breakdown()

    def status_breakdown(self) -> dict[str, int]:
        """Exact per-status counts of every logical cell of this analysis."""
        if self._breakdown is not None:
            return self._breakdown
        self.flush()
        breakdown = {status: 0 for status in (STATUS_EMPTY, STATUS_KEY, STATUS_NON,
                                             STATUS_UNRATED, STATUS_PII, STATUS_REVIEW)}
        for (blob,) in self._exec(
            "SELECT status FROM row_status WHERE analysis_id = ?", (self.analysis_id,)
        ):
            codes = bytearray(blob)
            empty = codes.count(CODE_EMPTY)
            key = codes.count(CODE_KEY)
            detail = codes.count(CODE_DETAIL)
            breakdown[STATUS_EMPTY] += empty
            breakdown[STATUS_KEY] += key
            breakdown[STATUS_NON] += len(codes) - empty - key - detail
        for status, count in self._exec(
            "SELECT cell_status, COUNT(*) FROM cell_info WHERE analysis_id = ? GROUP BY cell_status",
            (self.analysis_id,),
        ):
            breakdown[status] = breakdown.get(status, 0) + count
        self._breakdown = breakdown
        return breakdown

    def logical_cell_count(self) -> int:
        self.flush()
        return self._exec(
            "SELECT COALESCE(SUM(width), 0) FROM row_status WHERE analysis_id = ?",
            (self.analysis_id,),
        ).fetchone()[0]

    def verify_completeness(self) -> dict:
        """Check that every logical cell has exactly one verdict."""
        breakdown = self.status_breakdown()
        stored = sum(breakdown.values())
        logical = self.logical_cell_count()
        return {
            "logical_cells": logical,
            "verdict_cells": stored,
            "missing_cells": logical - stored,
            "complete": logical == stored,
            "status_counts": breakdown,
        }

    # Legacy name kept so existing callers keep working.
    write_row_verdicts = aggregate_row_verdicts

    # ---------------------------------------------------------------- read side
    def _cell_from_info(self, raw: tuple) -> dict:
        return dict(zip(VERDICT_FIELDS, raw))

    def _derived_cell(self, row: int, index: int, code: int, column: str) -> dict:
        """The verdict of a cell that needs no record: the status byte is it."""
        status = code_to_status(code)
        return {
            "row": row, "column_index": index, "column": column,
            "cell_status": status, "privacy_types": "",
            "rule_verdict": code_to_rule_verdict(code),
            "ai_verdict": "", "final_verdict": status, "confidence": 0.0,
            "decision_method": "KEY" if status == STATUS_KEY else "규칙",
            "needs_review": False,
            "masked_value": "", "evidence": code_to_evidence(code),
        }

    def _derived_cells(self, row: int, blob: bytes, column_names: dict[int, str],
                       status_wanted: str | None):
        codes = bytearray(blob)
        for index, code in enumerate(codes):
            if code_to_status(code) != status_wanted:
                continue
            yield self._derived_cell(row, index, code, column_names.get(index, f"COL_{index}"))

    def _column_names(self) -> dict[int, str]:
        return {index: name for index, name in self._exec(
            "SELECT column_index, column FROM layout_columns "
            "WHERE analysis_id = ? AND table_id = ?",
            (self.analysis_id, self.table_id),
        )}

    def iter_cell_verdicts(self, status: str | None = None, offset: int = 0,
                           limit: int | None = None) -> Iterable[dict]:
        """Stream the verdict of every logical cell, in row/column order.

        Statuses that carry evidence are read straight from `cell_info` with index
        paging. Derived statuses (빈 셀 / KEY 제외 / 비개인정보) are decoded from
        the status bytes, which is why the ledger is complete without one record
        per cell; with no status filter the two streams are merged in order so the
        caller sees exactly one verdict per logical cell.
        """
        self.flush()
        if status in INFO_STATUSES:
            query = INFO_SELECT + " AND cell_status = ? ORDER BY row, column_index"
            params: list = [self.analysis_id, status]
            if limit is not None:
                query += " LIMIT ? OFFSET ?"
                params.extend([limit, offset])
            for raw in self._exec(query, tuple(params)):
                yield self._cell_from_info(raw)
            return

        names = self._column_names()
        if status in DERIVED_STATUSES:
            rows = self._exec(
                "SELECT row, status FROM row_status WHERE analysis_id = ? ORDER BY row",
                (self.analysis_id,),
            )
            produced = 0
            for row_number, blob in rows:
                codes = bytearray(blob)
                matches = sum(1 for code in codes if code_to_status(code) == status)
                if produced + matches <= offset:
                    # Whole row is before the page: count it, do not materialise it.
                    produced += matches
                    continue
                for cell in self._derived_cells(row_number, blob, names, status):
                    if produced < offset:
                        produced += 1
                        continue
                    produced += 1
                    yield cell
                    if limit is not None and (produced - offset) >= limit:
                        return
            return

        # Merge: one verdict per logical cell, detail records taking their
        # authoritative status over the byte that merely points at them.
        # Deep pages must not walk every preceding cell, so when every row has
        # the same width the page start is computed directly (keyset seek).
        lo, hi, _ = self._exec(
            "SELECT COALESCE(MIN(width), 0), COALESCE(MAX(width), 0), COUNT(*) "
            "FROM row_status WHERE analysis_id = ?",
            (self.analysis_id,),
        ).fetchone()
        uniform_width = lo if lo == hi and lo > 0 else 0
        start_row = offset // uniform_width + 1 if uniform_width and offset else 1

        info_rows = self._exec(
            "SELECT row, column_index, column, cell_status, privacy_types, rule_verdict, "
            "ai_verdict, final_verdict, confidence, decision_method, needs_review, "
            "masked_value, evidence FROM cell_info WHERE analysis_id = ? AND row >= ? "
            "ORDER BY row, column_index", (self.analysis_id, start_row)
        )
        produced = offset - (offset % uniform_width) if uniform_width and offset else 0
        pending = next(info_rows, None)
        for row_number, blob in self._exec(
            "SELECT row, status FROM row_status WHERE analysis_id = ? AND row >= ? ORDER BY row",
            (self.analysis_id, start_row),
        ):
            codes = bytearray(blob)
            detail: dict[int, dict] = {}
            while pending is not None and pending[0] == row_number:
                detail[pending[1]] = self._cell_from_info(pending)
                pending = next(info_rows, None)
            for index, code in enumerate(codes):
                if code == CODE_DETAIL:
                    cell = detail.get(index)
                    if cell is None:
                        # Invariant broken: the byte promises a record that is not
                        # there. Report the cell as 미평가 rather than dropping it.
                        cell = self._derived_cell(row_number, index, CODE_DETAIL,
                                                 names.get(index, f"COL_{index}"))
                else:
                    cell = self._derived_cell(row_number, index, code,
                                            names.get(index, f"COL_{index}"))
                if produced < offset:
                    produced += 1
                    continue
                produced += 1
                yield cell
                if limit is not None and (produced - offset) >= limit:
                    return

    def verdict_count(self, status: str | None = None) -> int:
        if status is None:
            return self.logical_cell_count()
        breakdown = self.status_breakdown()
        return breakdown.get(status, 0)

    def human_review_count(self) -> dict:
        """Cells/rows still awaiting a human decision after rules and AI merge."""
        self.flush()
        row = self._exec(
            "SELECT COUNT(*) AS cells, COUNT(DISTINCT row) AS rows FROM cell_detail "
            "WHERE analysis_id = ? AND needs_review = 1",
            (self.analysis_id,),
        ).fetchone()
        return {"cells": int(row[0]), "rows": int(row[1])}

    def candidate_count(self, statuses: tuple[str, ...]) -> int:
        self.flush()
        marks = ",".join("?" for _ in statuses)
        return self._exec(
            f"SELECT COUNT(*) FROM cell_info WHERE analysis_id = ? AND cell_status IN ({marks})",
            (self.analysis_id, *statuses),
        ).fetchone()[0]

    def iter_candidates(self, statuses: tuple[str, ...], limit: int) -> Iterable[dict]:
        """Evenly sampled cells that still need a judgement, spread over the file.

        Taking the first `limit` candidates would only ever inspect the top of the
        file, so candidates are strided: every `total // limit`-th one is chosen.
        The sample is deterministic for the same input.
        """
        self.flush()
        if limit <= 0:
            return
        marks = ",".join("?" for _ in statuses)
        params = (self.analysis_id, *statuses)
        total = self._exec(
            f"SELECT COUNT(*) FROM cell_info WHERE analysis_id = ? AND cell_status IN ({marks})",
            params,
        ).fetchone()[0]
        if total == 0:
            return
        stride = max(1, total // limit)
        position = 0
        for raw in self._exec(
            f"{INFO_SELECT} AND cell_status IN ({marks}) ORDER BY column_index, row", params
        ):
            if position % stride == 0:
                yield self._cell_from_info(raw)
            position += 1
            if position >= limit * stride:
                break

    def iter_raw_cells(self) -> Iterable[dict]:
        """Every cell the rule engine matched, oldest first, verdict included."""
        self.flush()
        for raw in self._exec(
            "SELECT id, row, column_index, column, type_key, display_name, raw_confidence, "
            "masked_value, evidence, confirmed, masked, sensitivity, priority, name_hint, "
            "cells_checked, decision, confidence, needs_review, column_support "
            "FROM cell_detail WHERE analysis_id = ? ORDER BY row, column_index",
            (self.analysis_id,),
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
        query = CELL_SELECT + " AND analysis_id = ?"
        params: list = [self.analysis_id]
        if limit is not None:
            query += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        for raw in self._exec(query, params):
            yield dict(zip(CELL_FIELDS, raw))

    def cell_count(self) -> int:
        """Verdict-bearing cell records stored for this analysis - what Export must match."""
        return self._exec(
            "SELECT COUNT(*) FROM cell_detail WHERE analysis_id = ? AND decision <> ''",
            (self.analysis_id,),
        ).fetchone()[0]

    def row_count(self) -> int:
        return self._exec(
            "SELECT COUNT(*) FROM row_detail WHERE analysis_id = ?", (self.analysis_id,)
        ).fetchone()[0]

    def row_cell_count(self, row: int) -> int:
        return self._exec(
            "SELECT COUNT(*) FROM cell_detail WHERE analysis_id = ? AND row = ? AND decision <> ''",
            (self.analysis_id, row),
        ).fetchone()[0]

    def cells_for_row(self, row: int, offset: int = 0, limit: int | None = None) -> Iterable[dict]:
        params: list = [self.analysis_id, row]
        query = ("SELECT row, column_index, column, type_key, display_name, decision, confidence, "
                 "masked_value, evidence, needs_review, column_support, name_hint, cells_checked, "
                 "confirmed, masked, sensitivity, priority FROM cell_detail "
                 "WHERE analysis_id = ? AND row = ? AND decision <> '' ORDER BY column_index")
        if limit is not None:
            query += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        for raw in self._exec(query, params):
            yield dict(zip(CELL_FIELDS, raw))

    def cells_between_rows(self, first_row: int, last_row: int,
                           limit: int | None = None) -> Iterable[dict]:
        params: list = [self.analysis_id, first_row, last_row]
        query = CELL_SELECT_RANGE
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        for raw in self._exec(query, params):
            yield dict(zip(CELL_FIELDS, raw))

    def iter_rows(self, offset: int = 0, limit: int | None = None) -> Iterable[dict]:
        query = ROW_SELECT
        params: list = [self.analysis_id]
        if limit is not None:
            query += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        for raw in self._exec(query, params):
            yield self._row_dict(raw)

    def iter_cells_with_row_context(self) -> Iterable[dict]:
        for raw in self._exec(CELL_WITH_ROW, (self.analysis_id,)):
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

    def iter_row_verdicts(self, offset: int = 0, limit: int | None = None) -> Iterable[dict]:
        self.flush()
        query = (
            "SELECT row, total_cells, empty_cells, key_cells, pii_cells, review_cells, "
            "non_pii_cells, unrated_cells, status, has_pii FROM row_verdict WHERE analysis_id = ? "
            "ORDER BY row"
        )
        params: list = [self.analysis_id]
        if limit is not None:
            query += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        for raw in self._exec(query, params):
            yield {
                "row": raw[0], "total_cells": raw[1], "empty_cells": raw[2], "key_cells": raw[3],
                "pii_cells": raw[4], "review_cells": raw[5], "non_pii_cells": raw[6],
                "unrated_cells": raw[7], "status": raw[8], "has_pii": bool(raw[9]),
            }

    def row_verdict_count(self) -> int:
        return self._exec(
            "SELECT COUNT(*) FROM row_verdict WHERE analysis_id = ?", (self.analysis_id,)
        ).fetchone()[0]

    def review_row_count(self) -> int:
        return self._exec(
            "SELECT COUNT(*) FROM row_detail WHERE analysis_id = ? AND needs_review = 1",
            (self.analysis_id,),
        ).fetchone()[0]

    def review_rows(self, limit: int | None = None) -> list[dict]:
        """Review-worthy rows across the whole file (deterministic uniform sample)."""
        total = self.review_row_count()
        if limit is not None and limit <= 0:
            return []
        query = ROW_SELECT + " AND needs_review = 1 ORDER BY row"
        if limit is None or total <= limit:
            return [self._row_dict(raw) for raw in self._exec(query, (self.analysis_id,))]
        picks = {i * (total - 1) // (limit - 1) for i in range(limit)} if limit > 1 else {0}
        out: list[dict] = []
        for ordinal, raw in enumerate(self._exec(query, (self.analysis_id,))):
            if ordinal in picks:
                out.append(self._row_dict(raw))
        return out

    def iter_record_keys(self) -> Iterable[dict]:
        """KEY fingerprint per source row: rows are never merged, only grouped."""
        self.flush()
        for row, fingerprint, row_key in self._exec(
            "SELECT row, key_fingerprint, row_key FROM row_status WHERE analysis_id = ? ORDER BY row",
            (self.analysis_id,),
        ):
            yield {"row": row, "key_fingerprint": fingerprint, "row_key": row_key,
                   "key_defined": bool(fingerprint)}

    def iter_records(self, offset: int = 0, limit: int | None = None) -> Iterable[dict]:
        """One record per source row: its KEY fingerprint plus its cell verdict mix.

        Row identity is the physical row, so rows that share (or lack) a KEY value
        stay separate records; the fingerprint only groups identical KEY values.
        """
        self.flush()
        query = (
            "SELECT v.row, r.key_fingerprint, r.row_key, v.total_cells, v.key_cells, v.empty_cells, "
            "v.pii_cells, v.review_cells, v.non_pii_cells, v.unrated_cells, v.status, v.has_pii "
            "FROM row_verdict v JOIN row_status r "
            "  ON r.analysis_id = v.analysis_id AND r.table_id = v.table_id AND r.row = v.row "
            "WHERE v.analysis_id = ? ORDER BY v.row"
        )
        params: list = [self.analysis_id]
        if limit is not None:
            query += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        for raw in self._exec(query, tuple(params)):
            yield {
                "row": raw[0], "key_fingerprint": raw[1], "row_key": raw[2],
                "total_cells": raw[3], "key_cells": raw[4], "empty_cells": raw[5],
                "pii_cells": raw[6], "review_cells": raw[7], "non_pii_cells": raw[8],
                "unrated_cells": raw[9], "status": raw[10], "has_pii": bool(raw[11]),
            }

    def record_count(self) -> int:
        self.flush()
        return self._exec(
            "SELECT COUNT(*) FROM row_verdict WHERE analysis_id = ?", (self.analysis_id,)
        ).fetchone()[0]

    def group_count(self) -> int:
        self.flush()
        return self._exec(
            "SELECT COUNT(*) FROM pii_group WHERE analysis_id = ?", (self.analysis_id,)
        ).fetchone()[0]

    def iter_groups(self, limit: int | None = None, offset: int = 0) -> Iterable[dict]:
        """Cells that share a personal-data fingerprint, aggregated in SQLite."""
        # The masked sample is a display aid only: the group identity is the HMAC
        # fingerprint, and the raw value is never stored anywhere.
        query = (
            "SELECT g.fingerprint, g.pii_type, g.occurrences, g.distinct_rows, g.distinct_records, "
            "g.columns, g.key_samples, g.first_row, g.last_row, "
            "(SELECT c.masked_value FROM cell_info c "
            " WHERE c.analysis_id = g.analysis_id AND c.table_id = g.table_id "
            "   AND c.fingerprint = g.fingerprint AND c.masked_value <> '' LIMIT 1) "
            "FROM pii_group g WHERE g.analysis_id = ? ORDER BY g.occurrences DESC, g.pii_type"
        )
        params: list = [self.analysis_id]
        if limit is not None:
            query += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        cursor = self._exec(query, tuple(params))
        for raw in cursor:
            yield {"fingerprint": raw[0], "pii_type": raw[1], "occurrences": raw[2],
                   "distinct_rows": raw[3], "distinct_records": raw[4],
                   "columns": raw[5].split(",") if raw[5] else [],
                   "key_samples": json.loads(raw[6] or "[]"),
                   "first_row": raw[7], "last_row": raw[8], "masked_sample": raw[9]}
            if limit is not None:
                limit -= 1
                if limit <= 0:
                    return

    def build_groups(self) -> int:
        """Aggregate cells that carry the same personal-data fingerprint.

        Grouping is by HMAC fingerprint only: two rows that happen to share a KEY
        value are still two rows, and a group never means "the same person". The
        original value is never stored, so a group is described by its fingerprint,
        the types it matched, how often it occurs and where it appears. Samples are
        capped so one popular value cannot make the report megabytes large.
        """
        self.flush()
        self._exec("DELETE FROM pii_group WHERE analysis_id = ?", (self.analysis_id,))
        self._exec(
            "INSERT OR REPLACE INTO pii_group (analysis_id, table_id, fingerprint, pii_type, "
            "occurrences, distinct_rows, distinct_records, columns, key_samples, first_row, last_row) "
            "SELECT c.analysis_id, c.table_id, c.fingerprint, "
            "GROUP_CONCAT(DISTINCT c.privacy_types), COUNT(*), COUNT(DISTINCT c.row), "
            "COUNT(DISTINCT NULLIF(r.key_fingerprint, '')), GROUP_CONCAT(DISTINCT c.column), '', "
            "MIN(c.row), MAX(c.row) "
            "FROM cell_info c JOIN row_status r "
            "  ON r.analysis_id = c.analysis_id AND r.table_id = c.table_id AND r.row = c.row "
            "WHERE c.analysis_id = ? AND c.cell_status = ? AND c.fingerprint <> '' "
            "GROUP BY c.table_id, c.fingerprint",
            (self.analysis_id, STATUS_PII),
        )
        # KEY fingerprints only exist when the user designated KEY columns; with no
        # KEY there is nothing to sample, and the per-group scan is skipped entirely.
        if self.key_cells:
            # Only the first 10 KEY fingerprints of a group are kept; the count above
            # is exact, the sample is there to show which records are affected.
            self._exec(
                "UPDATE pii_group SET key_samples = ("
                "  SELECT json_group_array(k) FROM ("
                "    SELECT DISTINCT r.key_fingerprint AS k FROM cell_info c "
                "    JOIN row_status r ON r.analysis_id = c.analysis_id "
                "      AND r.table_id = c.table_id AND r.row = c.row "
                "    WHERE c.analysis_id = pii_group.analysis_id "
                "      AND c.table_id = pii_group.table_id "
                "      AND c.fingerprint = pii_group.fingerprint AND r.key_fingerprint <> '' "
                "    LIMIT 10"
                "  ) "
                ") WHERE analysis_id = ?",
                (self.analysis_id,),
            )
        self._commit()
        return self._exec(
            "SELECT COUNT(*) FROM pii_group WHERE analysis_id = ?", (self.analysis_id,)
        ).fetchone()[0]

    @staticmethod
    def _row_dict(raw: tuple) -> dict:
        record = dict(zip(ROW_FIELDS, raw))
        for key in ("confirmed_pii", "possible_pii", "column_names", "column_indexes", "ai_review"):
            record[key] = json.loads(record[key] or "[]") if key != "ai_review" else json.loads(record[key] or "{}")
        record["has_pii"] = bool(record["has_pii"])
        record["needs_review"] = bool(record["needs_review"])
        return record

    def stats(self) -> dict:
        self.flush()
        completeness = self.verify_completeness()
        candidates = self.review_row_count()
        return {
            "analysis_id": self.analysis_id,
            "table_id": self.table_id,
            "detail_rows_stored": self.rows_with_detail,
            "detail_cells_stored": self.decided_cells,
            "raw_cell_hits": self.raw_cell_count,
            "verdict_cells": completeness["verdict_cells"],
            "verdict_status": completeness["status_counts"],
            "logical_cells": completeness["logical_cells"],
            "verdict_complete": completeness["complete"],
            "missing_cells": completeness["missing_cells"],
            "key_cells": self.key_cells,
            "empty_cells": self.empty_cells,
            "derived_cells": completeness["logical_cells"] - self._exec(
                "SELECT COUNT(*) FROM cell_info WHERE analysis_id = ?", (self.analysis_id,)
            ).fetchone()[0],
            "detail_store_bytes": self.path.stat().st_size if self.path.exists() else 0,
            "detail_store_path": str(self.path),
            "review_candidates": candidates,
            "pii_groups": self._exec(
                "SELECT COUNT(*) FROM pii_group WHERE analysis_id = ?", (self.analysis_id,)
            ).fetchone()[0],
        }

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.flush()
        self.commit()
        # Folding the WAL back into the main file releases the -wal/-shm sidecars
        # so cleanup can remove the whole directory.
        try:
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass
        self.conn.close()

    def cleanup(self) -> dict:
        """Close the database and, for temporary stores, remove it with its sidecars."""
        from app.core import temp_store

        temp_store.unregister_store(self)
        self.cleaned = True
        try:
            self.close()
        except sqlite3.Error:
            pass
        status = {"detail_store_path": str(self.path), "detail_store_removed": False,
                  "temporary": bool(self.temp_dir)}
        if self.temp_dir:
            for sidecar in (self.path, Path(str(self.path) + "-wal"), Path(str(self.path) + "-shm")):
                try:
                    sidecar.unlink()
                except OSError:
                    pass
            # The temp directory itself is disposable, whether or not it is the
            # same object as the store's parent.
            try:
                self.temp_dir.rmdir()
            except OSError:
                pass
            status["detail_store_removed"] = not self.path.exists()
            status["temp_dir_removed"] = not self.temp_dir.exists()
        return status
