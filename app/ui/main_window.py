"""메인 창: 파일 분석 / 다중 파일 / 이력 / 결과 / 하드웨어 / 설정 / 디버그 / 프로그램 정보."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app import __version__
from app.ai.gpu_detector import detect_hardware
from app.config.settings import AnalysisConfig
from app.export.csv_exporter import write_csv
from app.export.hit_exporter import (write_cell_records, write_group_records, write_hit_rows,
                                     write_record_records, write_row_records)
from app.export.txt_exporter import write_txt
from app.ui.analysis_worker import AnalysisWorker
from app.ui.analysis_window import AnalysisWindow
from app.ui.file_selector import FileSelector
from app.ui.key_selector import KeySelector
from app.ui.lifecycle import unregister_thread
from app.ui.metadata_window import MetadataWindow
from app.ui.preview_window import PreviewWindow
from app.ui.result_window import ResultWindow
from app.ui.settings_window import SettingsWindow
from app.utils.logger import Diagnostics, get_logger


class MainWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"개인정보 탐지 및 분석 시스템 (Privacy Scanner {__version__})")
        self.resize(1000, 700)
        self.config = AnalysisConfig.load()
        self.hardware = detect_hardware()
        self.history: list = []
        self.diagnostics: list[dict] = []
        # Detail stores are opened per analysis so the user can keep browsing the
        # full cell detail; temporary ones are removed when the app closes.
        self._stores: list = []

        self.mode_box = QWidget()
        mode_layout = QHBoxLayout(self.mode_box)
        mode_layout.addWidget(QLabel("분석 유형"))
        self.mode_table_button = QPushButton("A: 실제 테이블 데이터 분석")
        self.mode_metadata_button = QPushButton("B: DB 메타정보 분석")
        self.mode_table_button.clicked.connect(lambda: self.select_mode("table"))
        self.mode_metadata_button.clicked.connect(lambda: self.select_mode("metadata"))
        mode_layout.addWidget(self.mode_table_button)
        mode_layout.addWidget(self.mode_metadata_button)
        self.mode_label = QLabel("선택: 실제 테이블 데이터 분석 (Function A)")
        mode_layout.addWidget(self.mode_label)
        self.mode_box.setStyleSheet("QPushButton {padding: 8px 14px;}")

        tabs = QTabWidget()
        self.analysis_tab = QWidget()
        selector_layout = QVBoxLayout(self.analysis_tab)
        self.selector = FileSelector()
        selector_layout.addWidget(self.selector)
        preview_button = QPushButton("미리보기 (20행)")
        preview_button.clicked.connect(self.show_preview)
        self.run_button = QPushButton("분석 실행")
        self.run_button.clicked.connect(self.run_analysis)
        self.cancel_button = QPushButton("분석 취소")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_analysis)
        selector_layout.addWidget(preview_button)
        selector_layout.addWidget(self.run_button)
        selector_layout.addWidget(self.cancel_button)
        self.key_selector = KeySelector()
        selector_layout.addWidget(self.key_selector)
        self.progress = AnalysisWindow(self.hardware)
        selector_layout.addWidget(self.progress)
        tabs.addTab(self.analysis_tab, "실제 테이블 데이터 (Function A)")

        self.metadata_window = MetadataWindow(self.config, self.hardware)
        tabs.addTab(self.metadata_window, "DB 메타정보 (Function B)")

        self.result_window = ResultWindow(self.config)
        tabs.addTab(self.result_window, "분석 결과")

        self.history_table = QTableWidget()
        self.history_table.setColumnCount(6)
        self.history_table.setHorizontalHeaderLabels(["분석일시", "분석 유형", "파일", "행 수",
                                                     "개인정보 컬럼", "위험도"])
        tabs.addTab(self.history_table, "분석 이력")

        hardware_widget = QWidget()
        hardware_layout = QVBoxLayout(hardware_widget)
        hw = self.hardware
        if hw.gpu_model:
            gpu_line = f"GPU: {hw.gpu_vendor} {hw.gpu_model}"
            if hw.vram_total_gb:
                gpu_line += f" (VRAM {hw.vram_total_gb:.1f}GB)"
            if hw.gpu_compute_arch:
                gpu_line += f" [{hw.gpu_compute_arch}]"
            if hw.backend == "cpu":
                reason = (hw.backend_detail or {}).get("reason", "no GPU backend library installed")
                gpu_line += f" -> CPU 추론 ({reason})"
            else:
                gpu_line += " -> GPU 추론 가능"
        elif hw.backend != "cpu":
            gpu_line = f"GPU: {hw.backend} backend -> GPU 추론 가능"
        else:
            gpu_line = "GPU: 없음 -> CPU 추론"
        summary = (
            f"OS: {hw.os_name} | CPU: {hw.cpu_model} ({hw.cpu_count} cores)\n"
            f"RAM: total {hw.total_ram_gb:.1f}GB / available {hw.available_ram_gb:.1f}GB\n"
            f"{gpu_line}\n"
            f"선택 backend: {hw.backend}"
        )
        hardware_layout.addWidget(QLabel(summary))
        for note in hw.notes:
            hardware_layout.addWidget(QLabel(note))
        tabs.addTab(hardware_widget, "하드웨어 정보")

        self.settings_window = SettingsWindow(self.config, self.hardware)
        tabs.addTab(self.settings_window, "환경 설정")

        debug_widget = QWidget()
        self.debug_label = QLabel("디버그 정보 없음 (환경 설정에서 디버그 로그를 활성화하세요).")
        QVBoxLayout(debug_widget).addWidget(self.debug_label)
        tabs.addTab(debug_widget, "디버그 정보")

        tabs.addTab(QLabel("Privacy Scanner - 폐쇄망 독립형 개인정보 탐지 시스템\n"
                           "Local LLM (llama.cpp) + 규칙 기반 엔진, 외부 네트워크 사용 없음"), "프로그램 정보")

        main_layout = QVBoxLayout(self)
        main_layout.addWidget(self.mode_box)
        main_layout.addWidget(tabs)
        self.tabs = tabs
        self.select_mode("table")

    def select_mode(self, mode: str) -> None:
        """Two analysis functions are separate flows: switching never mixes inputs."""
        self.config.analysis_kind = mode
        if mode == "table":
            self.mode_table_button.setStyleSheet("background: #2f6f5f; color: white;")
            self.mode_metadata_button.setStyleSheet("")
            self.mode_label.setText("선택: 실제 테이블 데이터 분석 (Function A)")
            self.tabs.setCurrentWidget(self.analysis_tab)
        else:
            self.mode_metadata_button.setStyleSheet("background: #2f6f5f; color: white;")
            self.mode_table_button.setStyleSheet("")
            self.mode_label.setText("선택: DB 메타정보 분석 (Function B)")
            self.tabs.setCurrentWidget(self.metadata_window)

    def show_preview(self) -> None:
        if not self.selector.files:
            return
        preview = PreviewWindow(self.selector.files[0])
        preview.show()
        self._preview = preview

    def run_analysis(self) -> None:
        """Start the analysis off the GUI thread.

        The scan itself runs in `AnalysisWorker`; the window only receives signals,
        so it stays responsive (and closable) while millions of cells are scanned.
        """
        if not self.selector.files or getattr(self, "_worker", None) is not None:
            return
        # Apply the 환경 설정 tab before running so batch size / threads / debug
        # selections actually reach the analysis engine.
        self.settings_window.apply()
        self.config.key_columns = self.key_selector.selected_keys()
        config = self.config
        diagnostics = Diagnostics(enabled=config.debug)
        self.run_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress.update_state(**{"분석 상태": "분석 시작 (백그라운드 thread)"})

        self.key_selector.set_files(self.selector.files)
        worker = AnalysisWorker(self.selector.files, config, self.hardware, diagnostics)
        worker.progress.connect(self.progress.update_state)
        worker.file_finished.connect(self._record_history)
        worker.finished.connect(self._analysis_finished)
        worker.failed.connect(lambda message: self.progress.update_state(**{"분석 오류": message}))
        self._worker = worker
        self._diagnostics_holder = diagnostics
        worker.start()

    def cancel_analysis(self) -> None:
        worker = getattr(self, "_worker", None)
        if worker is not None:
            worker.request_cancel()
            self.progress.update_state(**{"분석 상태": "취소 요청됨 - 부분 결과 보존"})

    def wait_for_analysis(self, timeout_ms: int = 120_000) -> list:
        """Block until the worker finishes (used by headless QA, not by users)."""
        worker = getattr(self, "_worker", None)
        if worker is None:
            return []
        worker.wait(timeout_ms)
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()
        return worker.results

    def _record_history(self, result) -> None:
        self.history.append(result)
        if getattr(result, "detail_store", None) is not None:
            self._stores.append(result.detail_store)
        engine = getattr(self._worker, "engine", None)
        self.history_table.setRowCount(len(self.history))
        row_index = len(self.history) - 1
        highest = max((r.risk_level for r in result.results), default="없음")
        for column, value in enumerate([
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            "테이블 데이터",
            Path(result.path).name,
            str(result.rows_parsed),
            str(len({r.column for r in result.results})),
            highest,
        ]):
            self.history_table.setItem(row_index, column, QTableWidgetItem(value))
        cell_audit = result.ai_audit.get("cell", {})
        self.progress.update_state(
            **{
                "처리 행 수": result.rows_parsed,
                "전체 행 수": result.total_rows,
                "처리 Cell 수": f"{result.cell_counts.get('cells_inspected', 0):,}",
                "Cell 판정 저장": f"{result.cell_counts.get('verdict_cells', 0):,} (개인정보 {result.cell_counts.get('pii_cells', 0):,} / 검토 {result.cell_counts.get('review_cells', 0):,})",
                "개인정보 Row 수": f"{result.cell_counts.get('detail_rows_stored', 0):,} (RAM 캐시 {result.cell_counts.get('detail_cache_rows', 0):,})",
                "Cell 상세 저장": f"{result.cell_counts.get('detail_cells_stored', 0):,} (RAM 캐시 {result.cell_counts.get('detail_cache_cells', 0):,} Cell)",
                "AI 판단(컬럼)": str(result.ai_audit.get("attempts", 0)),
                "AI 판단(Cell)": str(cell_audit.get("attempted", 0)),
                "AI 판단(Row)": str(result.ai_audit.get("row", {}).get("attempts", 0)),
                "AI batch": str(cell_audit.get("batches", 0)),
                "탐지된 개인정보 유형": len({r.privacy_type for r in result.results}),
                "발견된 컬럼 수": len({r.column for r in result.results}),
                "현재 분석 컬럼": max(result.results, key=lambda r: r.hits).column if result.results else "-",
                "메모리 사용량": f"{engine.memory_usage()['rss_mb']:.1f} MB" if engine else "-",
                "AI 실행 상태": result.ai_summary.get("status", "-"),
                "추론 방식": result.ai_summary.get("backend", "-"),
                "AI 모델": result.ai_summary.get("model") or "없음",
                "AI 판정 수": str(result.ai_summary.get("decisions", 0)),
                "AI 생략/오류": result.ai_summary.get("skipped_reason") or result.ai_summary.get("error") or "-",
            }
        )

    def _analysis_finished(self, results: list) -> None:
        self.run_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.progress.update_state(**{"분석 상태": "완료"})
        output_dir = Path("results")
        output_dir.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        write_csv([r.as_dict() for result in results for r in result.results],
                  output_dir / f"privacy_results_{stamp}.csv")
        write_txt(results, output_dir / f"privacy_report_{stamp}.txt")
        write_hit_rows(results, output_dir / f"privacy_hits_{stamp}.csv")
        write_row_records(results, output_dir / f"privacy_rows_{stamp}.csv")
        write_cell_records(results, output_dir / f"privacy_cells_{stamp}.csv")
        write_record_records(results, output_dir / f"privacy_records_{stamp}.csv")
        write_group_records(results, output_dir / f"privacy_groups_{stamp}.csv")
        get_logger("gui").info(f"결과 Export: {stamp} (Cell hit {write_hit_rows.rows_written:,}건, "
                               f"Row {write_row_records.rows_written:,}건, "
                               f"Cell verdict {write_cell_records.rows_written:,}건)")
        self.result_window.show_results([r for result in results for r in result.results])
        self.result_window.show_rows(results)
        self.diagnostics = self._diagnostics_holder.events
        if self.config.debug:
            self.debug_label.setText("\n".join(
                f"[{e['ts']}s] {e['event']}: "
                + ", ".join(f"{k}={v}" for k, v in e.items() if k not in {"event", "ts"})
                for e in self.diagnostics[:50]
            ))
        else:
            self.debug_label.setText("디버그 정보 없음 (환경 설정에서 디버그 로그를 활성화하세요).")
        self._worker = None


    def run_metadata_analysis(self) -> None:
        """Function B entry point: metadata analysis in its own worker/thread."""
        if not self.metadata_window.selector.files or self.metadata_window._worker is not None:
            return
        self.settings_window.apply()
        self.metadata_window.run_metadata_analysis()

    def cleanup_stores(self) -> None:
        """Release every detail store; only temporary stores are deleted."""
        for store in self._stores:
            status = store.cleanup()
            get_logger("gui").info(
                f"detail store {status['detail_store_path']} "
                f"{'removed' if status['detail_store_removed'] else 'kept'}"
            )
        self._stores = []

    def shutdown(self) -> None:
        """Join every worker thread before Qt is allowed to destroy it."""
        worker = getattr(self, "_worker", None)
        if worker is not None and worker.isRunning():
            worker.request_cancel()
            worker.wait(60_000)
        if worker is not None:
            unregister_thread(worker)
        self._worker = None
        self.metadata_window.shutdown()
        self.cleanup_stores()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self.shutdown()
        event.accept()


def run_gui(args=None) -> int:
    import sys

    from PySide6.QtWidgets import QApplication

    from app.ui.lifecycle import close_widgets

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    code = app.exec()
    # Hand the widget graph back to Qt before the interpreter finalises: PySide
    # 6.12 double-frees widgets that Python garbage-collects first.
    close_widgets(window)
    return code
