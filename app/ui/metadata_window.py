"""Function B UI: metadata file + field mapping + table/column results."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.config.settings import AnalysisConfig
from app.metadata_analysis.field_mapping import ALL_ROLES, FieldMapping
from app.metadata_analysis.reports import (write_metadata_columns, write_metadata_review,
                                           write_metadata_summary, write_metadata_tables)
from app.ui.analysis_worker import MetadataWorker
from app.ui.file_selector import FileSelector
from app.ui.lifecycle import register_thread, unregister_thread

ROLE_LABELS = {
    "table_name": "테이블명", "table_comment": "테이블 설명", "column_name": "컬럼명",
    "column_comment": "컬럼 설명", "data_type": "자료형", "column_length": "컬럼 길이",
    "nullable": "NULL 허용", "primary_key": "PRIMARY KEY", "foreign_key": "FOREIGN KEY",
    "schema_name": "스키마", "db_name": "DB 이름",
}
PAGE_SIZE = 200


class MetadataWindow(QWidget):
    """Metadata-only analysis flow. No table rows are read here."""

    def __init__(self, config: AnalysisConfig, hardware) -> None:
        super().__init__()
        self.config = config
        self.hardware = hardware
        self.store = None
        self.summary: dict = {}
        self._page = 0
        self._worker: MetadataWorker | None = None

        layout = QVBoxLayout(self)

        intake_box = QGroupBox("기능 B: 데이터베이스 메타정보 분석 (실제 데이터 읽지 않음)")
        intake_layout = QVBoxLayout(intake_box)
        self.selector = FileSelector()
        intake_layout.addWidget(self.selector)

        self.mapping_label = QLabel("필드 매핑: 파일 선택 후 '필드 매핑 자동 인식' 누르세요")
        intake_layout.addWidget(self.mapping_label)
        self.mapping_table = QTableWidget(len(ALL_ROLES), 3)
        self.mapping_table.setHorizontalHeaderLabels(["필드 역할", "자동 인식", "직접 지정"])
        self.mapping_combos: dict[str, QComboBox] = {}
        for index, role in enumerate(ALL_ROLES):
            self.mapping_table.setItem(index, 0, QTableWidgetItem(ROLE_LABELS[role]))
            self.mapping_table.setItem(index, 1, QTableWidgetItem("-"))
            combo = QComboBox()
            combo.addItem("(사용하지 않음)")
            self.mapping_combos[role] = combo
            self.mapping_table.setCellWidget(index, 2, combo)
        intake_layout.addWidget(self.mapping_table)

        buttons = QHBoxLayout()
        self.detect_button = QPushButton("필드 매핑 자동 인식")
        self.detect_button.clicked.connect(self.detect_mapping)
        self.run_button = QPushButton("메타정보 분석 실행")
        self.run_button.clicked.connect(self.run_metadata_analysis)
        self.cancel_button = QPushButton("분석 취소")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_analysis)
        buttons.addWidget(self.detect_button)
        buttons.addWidget(self.run_button)
        buttons.addWidget(self.cancel_button)
        intake_layout.addLayout(buttons)
        layout.addWidget(intake_box)

        self.progress_text = QLabel("대기 중")
        layout.addWidget(self.progress_text)

        self.table_table = QTableWidget()
        self.table_table.setColumnCount(6)
        self.table_table.setHorizontalHeaderLabels(["테이블", "관련성", "개인정보 컬럼",
                                                   "후보 유형", "컬럼 수", "판정"])
        layout.addWidget(QLabel("테이블별 개인정보 관련성 (첫 200건)"))
        layout.addWidget(self.table_table)

        layout.addWidget(QLabel("컬럼별 판정 (페이지 200행, 전체는 SQLite에 저장)"))
        self.column_table = QTableWidget()
        self.column_table.setColumnCount(6)
        self.column_table.setHorizontalHeaderLabels(["테이블", "컬럼", "판정", "관련성",
                                                    "후보 유형", "근거"])
        layout.addWidget(self.column_table)
        pager = QHBoxLayout()
        self.prev_button = QPushButton("앞 페이지")
        self.next_button = QPushButton("다음 페이지")
        self.page_label = QLabel("페이지 1/0")
        self.prev_button.clicked.connect(self._previous_page)
        self.next_button.clicked.connect(self._next_page)
        pager.addWidget(self.prev_button)
        pager.addWidget(self.page_label)
        pager.addWidget(self.next_button)
        layout.addLayout(pager)

        self.export_button = QPushButton("보고서 Export (metadata_columns / tables / review)")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.export_reports)
        layout.addWidget(self.export_button)

    # ------------------------------------------------------------ field mapping
    def detect_mapping(self) -> None:
        if not self.selector.files:
            self.progress_text.setText("파일을 선택하세요")
            return
        from app.core.csv_detector import detect_format

        profile = detect_format(self.selector.files[0])
        mapping = FieldMapping.from_headers(profile.column_names)
        for index, role in enumerate(ALL_ROLES):
            auto = mapping.auto_mapping.get(role, {}).get("header", "-")
            self.mapping_table.setItem(index, 1, QTableWidgetItem(auto))
            combo = self.mapping_combos[role]
            combo.clear()
            combo.addItem("(사용하지 않음)")
            for header in profile.column_names:
                combo.addItem(header)
            if auto != "-":
                combo.setCurrentIndex(combo.findText(auto))
        detected = ", ".join(f"{ROLE_LABELS[role]}={entry['header']}"
                             for role, entry in mapping.auto_mapping.items())
        self.mapping_label.setText(
            f"자동 인식 {len(mapping.auto_mapping)}/{len(ALL_ROLES)}: {detected} | "
            f"매핑되지 않은 header: {', '.join(mapping.unmapped()) or '없음'}"
        )
        return mapping

    def mapping_overrides(self) -> dict:
        overrides = {}
        for role, combo in self.mapping_combos.items():
            header = combo.currentText()
            if header and header != "(사용하지 않음)":
                overrides[role] = header
        return {"overrides": overrides}

    # ------------------------------------------------------------ run
    def run_metadata_analysis(self) -> None:
        if not self.selector.files or self._worker is not None:
            return
        self.run_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress_text.setText("메타정보 분석 시작 (백그라운드 thread)")
        worker = MetadataWorker(self.selector.files, self.config, self.hardware,
                                mapping=self.mapping_overrides())
        worker.progress.connect(self._on_progress)
        worker.file_finished.connect(self._on_file_finished)
        worker.finished.connect(self._on_finished)
        worker.failed.connect(lambda message: self.progress_text.setText(f"오류: {message}"))
        self._worker = worker
        worker.start()

    def cancel_analysis(self) -> None:
        if self._worker is not None:
            self._worker.request_cancel()
            self.progress_text.setText("취소 요청됨 - 부분 결과 보존")

    def wait_for_analysis(self, timeout_ms: int = 60_000) -> list:
        worker = self._worker
        if worker is None:
            return []
        worker.wait(timeout_ms)
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()
        return worker.results

    def _on_progress(self, values: dict) -> None:
        if "처리 metadata Row" in values:
            self.progress_text.setText(f"Row {values['처리 metadata Row']:,} / "
                                       f"{values.get('전체 metadata Row', 0):,}건")
        elif "현재 분석 파일" in values:
            self.progress_text.setText(f"{values['현재 분석 파일']} ({values.get('파일 진행', '')})")

    def _on_file_finished(self, result: dict) -> None:
        counts = result["summary"]
        self.progress_text.setText(
            f"Row {counts['metadata_rows']:,} / 테이블 {counts['tables']:,} / "
            f"컬럼 {counts['columns']:,} (관련 {counts['relevant_columns']:,})"
        )

    def _on_finished(self, results: list) -> None:
        self.run_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        if not results:
            self.progress_text.setText("분석 결과 없음")
            return
        self.store = results[0]["store"]
        self.summary = dict(results[0]["summary"])
        self.summary["stamp"] = Path(self.selector.files[0]).stem if self.selector.files else "metadata"
        self.export_button.setEnabled(True)
        self.progress_text.setText(
            f"메타정보 분석 완료: 테이블 {self.summary['tables']:,} / "
            f"컬럼 {self.summary['columns']:,} / 개인정보 관련 {self.summary['relevant_columns']:,}"
        )
        self._page = 0
        self._load_tables()
        self._load_columns()

    # ------------------------------------------------------------ results
    def _load_tables(self) -> None:
        rows = self.store.conn.execute(
            "SELECT table_name, relevance, pii_column_count, candidate_types, column_count, status "
            "FROM table_verdicts WHERE analysis_id = ? ORDER BY relevance DESC, table_name LIMIT 200",
            (self.store.analysis_id,),
        ).fetchall()
        self.table_table.clear()
        self.table_table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            for column, value in enumerate(row):
                self.table_table.setItem(index, column, QTableWidgetItem(str(value)))

    def _column_total(self) -> int:
        return self.store.conn.execute(
            "SELECT COUNT(*) FROM column_verdicts WHERE analysis_id = ?", (self.store.analysis_id,)
        ).fetchone()[0]

    def _load_columns(self) -> None:
        total = self._column_total()
        offset = self._page * PAGE_SIZE
        rows = self.store.conn.execute(
            "SELECT table_name, column_name, verdict, confidence, type_candidates, rationale "
            "FROM column_verdicts WHERE analysis_id = ? "
            "ORDER BY confidence DESC, table_name, column_name LIMIT ? OFFSET ?",
            (self.store.analysis_id, PAGE_SIZE, offset),
        ).fetchall()
        self.column_table.clear()
        self.column_table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            for column, value in enumerate(row):
                self.column_table.setItem(index, column, QTableWidgetItem(str(value)))
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        self.page_label.setText(f"페이지 {self._page + 1}/{pages} (전체 {total:,} 컬럼)")

    def _previous_page(self) -> None:
        self._page = max(0, self._page - 1)
        self._load_columns()

    def _next_page(self) -> None:
        pages = max(1, (self._column_total() + PAGE_SIZE - 1) // PAGE_SIZE)
        self._page = min(pages - 1, self._page + 1)
        self._load_columns()

    def export_reports(self) -> list[Path]:
        output_dir = Path("results")
        output_dir.mkdir(exist_ok=True)
        stamp = self.summary.get("stamp") or "metadata"
        paths = [
            output_dir / f"metadata_columns_{stamp}.csv",
            output_dir / f"metadata_tables_{stamp}.csv",
            output_dir / f"metadata_review_{stamp}.csv",
            output_dir / f"metadata_report_{stamp}.txt",
        ]
        counts = {
            "columns": write_metadata_columns(self.store, paths[0]),
            "tables": write_metadata_tables(self.store, paths[1]),
            "review": write_metadata_review(self.store, paths[2]),
        }
        write_metadata_summary(self.store, paths[3], self.summary)
        self.progress_text.setText(
            f"Export: {', '.join(p.name for p in paths)} "
            f"(컬럼 {counts['columns']:,} / 테이블 {counts['tables']:,} / "
            f"검토 {counts['review']:,})")
        self.last_counts = counts
        return paths

    def cleanup_stores(self) -> None:
        if self.store is not None:
            self.store.cleanup()
            self.store = None

    def shutdown(self) -> None:
        """Join the worker thread before Qt is allowed to destroy it.

        Qt aborts the process when a QThread object is destructed while its
        thread is still running, so every analysis must be joined here.
        """
        worker = self._worker
        if worker is not None and worker.isRunning():
            worker.request_cancel()
            worker.wait(60_000)
        if worker is not None:
            unregister_thread(worker)
        self._worker = None
        self.cleanup_stores()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self.shutdown()
        event.accept()
