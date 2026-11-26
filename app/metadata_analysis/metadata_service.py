"""Function B pipeline: DB metadata file -> table/column relevance reports.

Intake is streamed (never materialised) and every verdict is written to SQLite,
so a 5,000-row metadata extract and a 500,000-row one use the same bounded RAM.
Reports are produced by streaming the store.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

from app.config.settings import AnalysisConfig
from app.core.csv_detector import detect_format
from app.core.csv_loader import CsvLoader, FileSettings
from app.core.detail_store import DetailStore
from app.core import temp_store
from app.metadata_analysis.column_classifier import (
    LOW, REVIEW, RELEVANT, ColumnMetadata, ColumnVerdict, score_metadata, table_relevance,
)
from app.metadata_analysis.field_mapping import FieldMapping
from app.utils.logger import Diagnostics, get_logger

logger = get_logger("metadata")

METADATA_COLUMN_FIELDS = ("table_name", "schema_name", "column_name", "column_comment",
                          "data_type", "column_length", "primary_key", "foreign_key",
                          "verdict", "relevance", "type_candidates", "method", "rationale",
                          "needs_confirmation")
METADATA_TABLE_FIELDS = ("table_name", "schema_name", "status", "relevance",
                         "pii_column_count", "review_column_count", "candidate_types",
                         "column_count", "rationale")
METADATA_REVIEW_FIELDS = ("table_name", "column_name", "data_type", "column_comment",
                          "verdict", "relevance", "type_candidates", "action")

ACTION_BY_VERDICT = {
    RELEVANT: "개인정보 관리 대상: 실제 값 존재 여부 확인",
    REVIEW: "샘플 데이터로 수동 확인",
    LOW: "관련 낮음 - 값 확인 후 제외 검토",
}


def _coerce_int(value: str) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _coerce_bool(value: str) -> bool:
    text = str(value or "").strip().lower()
    return text in ("1", "y", "yes", "true", "t", "pk", "primary", "not null", "notnull")


def parse_metadata_row(row: list[str], mapping: FieldMapping) -> ColumnMetadata:
    return ColumnMetadata(
        table=mapping.value(row, "table_name"),
        column=mapping.value(row, "column_name"),
        comment=mapping.value(row, "column_comment"),
        data_type=mapping.value(row, "data_type"),
        length=_coerce_int(mapping.value(row, "column_length")),
        nullable=_coerce_bool(mapping.value(row, "nullable")) if "nullable" in mapping.mapping else None,
        primary_key=_coerce_bool(mapping.value(row, "primary_key")),
        foreign_key=_coerce_bool(mapping.value(row, "foreign_key")),
        schema=mapping.value(row, "schema_name"),
        db=mapping.value(row, "db_name"),
    )


class MetadataService:
    """Streaming metadata analysis: read -> map -> score -> store -> report."""

    def __init__(self, config: AnalysisConfig, store: DetailStore, llm=None):
        self.config = config
        self.store = store
        self.llm = llm
        self.columns: dict[tuple[str, str], ColumnVerdict] = {}
        self.meta: dict[tuple[str, str], ColumnMetadata] = {}
        self.rows_read = 0
        self.error_rows = 0
        self.skipped_rows = 0
        self.empty_rows = 0
        self.range_scope: dict = {}
        self._started = time.perf_counter()
        self.rules_seconds = 0.0
        self.ai_seconds = 0.0

    def analyse(self, path: str, mapping=None, progress=None, should_stop=None,
                overrides: dict | None = None, tracker=None) -> dict:
        if tracker is not None:
            tracker.stage("format")
        profile = detect_format(path)
        overrides = dict(overrides or {})
        overrides.setdefault("table", self.config.table)
        overrides.setdefault("schema", self.config.schema)
        overrides.setdefault("db_type", self.config.db_type)
        settings = FileSettings.from_profile(profile, **overrides)
        loader = CsvLoader(settings, max_field_chars=self.config.max_field_chars,
                           max_cols=self.config.max_cols)
        loader.prepare()
        # The row/col caps apply to the metadata scan as well; rows past the cap are
        # counted as unexamined rather than assumed clean.
        row_limits = [limit for limit in (self.config.max_rows,
                                         self.config.sample_rows if self.config.mode == "sampling" else 0)
                      if limit and limit > 0]
        row_limit = min(row_limits) if row_limits else None
        if isinstance(mapping, dict):
            resolved = FieldMapping.from_headers(loader.column_names)
            for role, header in (mapping.get("overrides") or {}).items():
                resolved.set(role, header)
            mapping = resolved
        else:
            mapping = mapping or FieldMapping.from_headers(loader.column_names)
        ok, message = mapping.validate()
        if not ok:
            raise ValueError(message)

        rows_in_scope = loader.progress_total(row_limit)
        rows_excluded = max(0, (loader.total_rows or 0) - rows_in_scope) if row_limit else 0
        if tracker is not None:
            tracker.set_scope(max_rows=self.config.max_rows, max_cols=self.config.max_cols,
                              file_rows=loader.total_rows, file_columns=loader.source_width,
                              rows_in_scope=rows_in_scope, rows_excluded=rows_excluded,
                              columns_in_scope=loader.column_count,
                              columns_excluded=loader.columns_excluded,
                              complete=rows_excluded == 0 and loader.columns_excluded == 0,
                              text=(f"Row {rows_in_scope:,} / {loader.total_rows:,}, "
                                    f"Col {loader.column_count}"
                                    + (f" (미검사 Row {rows_excluded:,}, Col {loader.columns_excluded})"
                                       if rows_excluded or loader.columns_excluded else "")))
            tracker.stage("stream")
            tracker.row_progress(0, rows_in_scope)

        ambiguous: list[ColumnMetadata] = []
        batch: list[tuple] = []
        for _row_index, row in loader.iter_rows(row_limit):
            if should_stop is not None and should_stop():
                break
            try:
                meta = parse_metadata_row(row, mapping)
            except Exception as exc:  # a bad metadata row must not stop the scan
                self.error_rows += 1
                if self.error_rows <= 20:
                    logger.warning("메타정보 Row %d 파싱 실패: %s", _row_index, exc)
                continue
            if not meta.column:
                self.skipped_rows += 1
                continue
            self.rows_read += 1
            verdict = score_metadata(meta)
            self.columns[(meta.table, meta.column)] = verdict
            self.meta[(meta.table, meta.column)] = meta
            batch.append((self.store.analysis_id, meta.table, meta.column, meta.comment,
                          meta.data_type, meta.length, int(bool(meta.nullable)),
                          int(meta.primary_key), int(meta.foreign_key), meta.schema, meta.db))
            # Columns the rules cannot settle are the ones the model is asked about.
            if verdict.verdict == REVIEW or (verdict.verdict == LOW and meta.comment):
                ambiguous.append(meta)
            if len(batch) >= 1000:
                self._write_records(batch)
                batch.clear()
            if progress and self.rows_read % 500 == 0:
                progress(self.rows_read, rows_in_scope)
        if batch:
            self._write_records(batch)
        if tracker is not None:
            tracker.row_progress(self.rows_read, rows_in_scope)
        self.empty_rows = loader.empty_rows
        self.store.commit()
        # Every source row must be accounted for: analysed, unusable, or
        # genuinely empty. Nothing is allowed to vanish silently.
        rows_in_scope = loader.progress_total(row_limit)
        self.range_scope = {"rows_in_scope": rows_in_scope,
                            "rows_excluded": max(0, (loader.total_rows or 0) - rows_in_scope),
                            "columns_excluded": loader.columns_excluded}
        diagnostics = {"metadata_rows": self.rows_read, "skipped_rows": self.skipped_rows,
                       "parse_error_rows": self.error_rows, "empty_rows": self.empty_rows,
                       "rows_in_scope": rows_in_scope,
                       "rows_excluded": max(0, (loader.total_rows or 0) - rows_in_scope),
                       "columns_excluded": loader.columns_excluded}
        if (diagnostics["metadata_rows"] + diagnostics["skipped_rows"]
                + diagnostics["empty_rows"] + self.error_rows != rows_in_scope):
            logger.warning("메타정보 Row accounting mismatch: %s vs in-scope %s",
                           diagnostics, rows_in_scope)
        self.rules_seconds = time.perf_counter() - self._started

        if tracker is not None:
            tracker.stage("ai_column")
            tracker.ai_progress(candidates=len(ambiguous))
        ai_audit = self._apply_ai(ambiguous)
        if tracker is not None:
            tracker.stage("merge")
            tracker.ai_progress(judged=ai_audit.get("attempts", 0),
                                success=ai_audit.get("success", 0),
                                failed=ai_audit.get("failed", 0),
                                skipped=ai_audit.get("skipped", 0))
            tracker.stage("review")
        self._write_verdicts()
        tables = self._aggregate_tables()
        return {
            "mapping": mapping,
            "rows_read": self.rows_read,
            "error_rows": self.error_rows,
            "skipped_rows": self.skipped_rows,
            "columns": list(self.columns.values()),
            "tables": tables,
            "ai": ai_audit,
            "rules_seconds": self.rules_seconds,
            "ai_seconds": self.ai_seconds,
        }

    def _write_records(self, rows: list[tuple]) -> None:
        self.store._exec_many(
            "INSERT OR REPLACE INTO meta_records (analysis_id, table_name, column_name, "
            "column_comment, data_type, length, nullable, primary_key, foreign_key, schema_name, db_name) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        self.store._pending_commits += 1

    def _apply_ai(self, candidates: list[ColumnMetadata]) -> dict:
        """Judge the columns the rules could not settle with the local model."""
        audit = {"candidates": len(candidates), "attempted": 0, "success": 0, "failed": 0,
                 "skipped_reason": ""}
        if not candidates:
            return audit
        budget = max(0, self.config.max_ai_column_reviews)
        if budget == 0:
            audit["skipped_reason"] = "max_ai_column_reviews=0 (AI 검토 비활성화)"
            return audit
        if self.llm is None or not self.llm.load():
            detail = (getattr(self.llm, "skipped_reason", "") if self.llm else "") or "모델/Scorer 없음"
            audit["skipped_reason"] = f"Jev-Style AI 사용 불가 ({detail}) - 규칙 결과만 사용"
            return audit
        started = time.perf_counter()
        for meta in candidates[:budget]:
            verdict = self.columns.get((meta.table, meta.column))
            if verdict is None:
                continue
            state = {
                "schema": meta.schema or "-",
                "table": meta.table or "-",
                "column": meta.column,
                "column_type": meta.data_type or "TEXT",
                "column_comment": (meta.comment or "-")[:120],
                "column_length": meta.length if meta.length is not None else -1,
                "primary_key": int(meta.primary_key),
                "foreign_key": int(meta.foreign_key),
                "sample_count": 0,
                "masked_samples": [],
            }
            result = self.llm.classify_metadata(state)
            audit["attempted"] += 1
            if result.get("ok"):
                audit["success"] += 1
                verdict.verdict = result["verdict"]
                verdict.relevance = max(verdict.relevance, result["relevance"])
                verdict.method = "규칙+AI"
                verdict.type_candidates = sorted(set(verdict.type_candidates) | set(result["types"]))
                verdict.rationale.append(f"AI: {result['decision']} (p={result['confidence']:.2f})")
            else:
                audit["failed"] += 1
                # An AI failure never turns "검토 필요" into "비개인정보": the
                # column stays unresolved and the reason is recorded.
                verdict.rationale.append(f"AI 판정 실패: {result.get('error', '오류')}")
        if len(candidates) > budget:
            audit["skipped_reason"] = f"AI 후보 {len(candidates)}건 중 {budget}건만 검토"
        self.ai_seconds = time.perf_counter() - started
        return audit

    def _write_verdicts(self) -> None:
        rows = [(self.store.analysis_id, verdict.table, verdict.column, verdict.verdict,
                ",".join(verdict.type_candidates), round(verdict.relevance, 3), verdict.method,
                "; ".join(verdict.rationale), int(verdict.needs_confirmation))
                for verdict in self.columns.values()]
        self.store._exec_many(
            "INSERT OR REPLACE INTO column_verdicts (analysis_id, table_name, column_name, verdict, "
            "type_candidates, confidence, method, rationale, needs_confirmation) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            rows,
        )
        self.store._commit()

    def _aggregate_tables(self) -> dict[str, dict]:
        by_table: dict[str, list[ColumnVerdict]] = {}
        for verdict in self.columns.values():
            by_table.setdefault(verdict.table, []).append(verdict)
        tables: dict[str, dict] = {}
        rows = []
        for table, verdicts in by_table.items():
            summary = table_relevance(verdicts)
            schemas = sorted({meta.schema for (t, _), meta in self.meta.items()
                             if t == table and meta.schema})
            summary["schema"] = schemas[0] if schemas else ""
            tables[table] = summary
            rows.append((self.store.analysis_id, table, summary["relevance"],
                        summary["pii_column_count"], ",".join(summary["types"]),
                        summary["status"], summary["rationale"], len(verdicts)))
        if rows:
            self.store._exec_many(
                "INSERT OR REPLACE INTO table_verdicts (analysis_id, table_name, relevance, "
                "pii_column_count, candidate_types, status, rationale, column_count) "
                "VALUES (?,?,?,?,?,?,?,?)",
                rows,
            )
            self.store._commit()
        return tables

    def summary(self) -> dict:
        counts = {RELEVANT: 0, REVIEW: 0, LOW: 0}
        for verdict in self.columns.values():
            counts[verdict.verdict] = counts.get(verdict.verdict, 0) + 1
        return {
            "analysis_kind": "metadata",
            "metadata_rows": self.rows_read,
            "rows_excluded": self.range_scope.get("rows_excluded", 0),
            "columns_excluded": self.range_scope.get("columns_excluded", 0),
            "parse_error_rows": self.error_rows,
            "skipped_rows": self.skipped_rows,
            "empty_rows": self.empty_rows,
            "tables": len({key[0] for key in self.columns}),
            "columns": len(self.columns),
            "relevant_columns": counts[RELEVANT],
            "review_columns": counts[REVIEW],
            "low_columns": counts[LOW],
        }


def analyse_metadata_file(path: str, config: AnalysisConfig, llm=None,
                          mapping=None, progress=None, should_stop=None,
                          diagnostics: Diagnostics | None = None,
                          overrides: dict | None = None, tracker=None) -> dict:
    diagnostics = diagnostics or Diagnostics()
    temp_dir: Path | None = None
    if config.detail_store_path:
        detail_path = Path(config.detail_store_path)
    else:
        # Same lifecycle as Function A: an un-named store is temporary and is
        # therefore removed on completion, cancellation, exception or signal.
        temp_store.sweep_orphans()
        temp_dir = Path(tempfile.mkdtemp(prefix="piiscan-detail-"))
        detail_path = temp_dir / "detail.sqlite"
    analysis_id = f"meta-{Path(path).stem}-{time.strftime('%Y%m%d%H%M%S')}"
    store = DetailStore(detail_path, analysis_id, kind="metadata")
    store.temp_dir = temp_dir
    temp_store.register_store(store)
    service = MetadataService(config, store, llm)
    service._started = time.perf_counter()
    result = service.analyse(path, mapping=mapping, progress=progress,
                            should_stop=should_stop, overrides=overrides, tracker=tracker)
    diagnostics.record("metadata_scan", **service.summary())
    diagnostics.record("field_mapping", **result["mapping"].describe())
    return {
        "store": store,
        "service": service,
        "result": result,
        "summary": service.summary(),
        "diagnostics": diagnostics,
        "analysis_id": analysis_id,
    }
