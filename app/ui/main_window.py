"""메인 창: 파일 분석 / 다중 파일 / 이력 / 결과 / 하드웨어 / 설정 / 디버그 / 프로그램 정보."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import (
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app import __version__
from app.ai import create_ai_engine
from app.ai.gpu_detector import detect_hardware
from app.config.settings import AnalysisConfig
from app.core.analysis_service import analyse_file
from app.export.csv_exporter import write_csv
from app.export.hit_exporter import write_hit_rows, write_row_records
from app.export.txt_exporter import write_txt
from app.ui.analysis_window import AnalysisWindow
from app.ui.file_selector import FileSelector
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

        tabs = QTabWidget()
        self.analysis_tab = QWidget()
        selector_layout = QVBoxLayout(self.analysis_tab)
        self.selector = FileSelector()
        selector_layout.addWidget(self.selector)
        preview_button = QPushButton("미리보기 (20행)")
        preview_button.clicked.connect(self.show_preview)
        run_button = QPushButton("분석 실행")
        run_button.clicked.connect(self.run_analysis)
        selector_layout.addWidget(preview_button)
        selector_layout.addWidget(run_button)
        self.progress = AnalysisWindow(self.hardware)
        selector_layout.addWidget(self.progress)
        tabs.addTab(self.analysis_tab, "파일 분석")

        self.result_window = ResultWindow()
        tabs.addTab(self.result_window, "분석 결과")

        self.history_table = QTableWidget()
        self.history_table.setColumnCount(5)
        self.history_table.setHorizontalHeaderLabels(["분석일시", "파일", "행 수", "개인정보 컬럼", "위험도"])
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
        main_layout.addWidget(tabs)

    def show_preview(self) -> None:
        if not self.selector.files:
            return
        preview = PreviewWindow(self.selector.files[0])
        preview.show()
        self._preview = preview

    def run_analysis(self) -> None:
        if not self.selector.files:
            return
        # Apply the 환경 설정 tab before running so batch size / threads / debug
        # selections actually reach the analysis engine.
        self.settings_window.apply()
        config = self.config
        diagnostics = Diagnostics(enabled=config.debug)
        engine = create_ai_engine(config, self.hardware)
        results = []
        for index, path in enumerate(self.selector.files):
            self.progress.update_state(
                **{
                    "현재 분석 파일": path.name,
                    "전체 파일 수": len(self.selector.files),
                }
            )
            result = analyse_file(path, config, engine, diagnostics=diagnostics,
                                  progress=lambda done, total, cells=0: self.progress.update_state(
                                      **{"처리 행 수": done, "전체 행 수": total, "처리 Cell 수": cells}
                                  ))
            results.append(result)
            self.history.append(result)
            self.history_table.setRowCount(len(self.history))
            row_index = len(self.history) - 1
            highest = max((r.risk_level for r in result.results), default="없음")
            for column, value in enumerate([
                datetime.now().strftime("%Y-%m-%d %H:%M"),
                path.name,
                str(result.rows_parsed),
                str(len({r.column for r in result.results})),
                highest,
            ]):
                self.history_table.setItem(row_index, column, QTableWidgetItem(value))
            self.progress.update_state(
                **{
                    "처리 행 수": result.rows_parsed,
                    "전체 행 수": result.total_rows,
                    "처리 Cell 수": result.cell_counts.get("cells_inspected", 0),
                    "개인정보 Row 수": f"{result.cell_counts.get('detail_rows_stored', 0):,} (RAM 캐시 {result.cell_counts.get('detail_cache_rows', 0):,})",
                    "Cell 상세 저장": f"{result.cell_counts.get('detail_cells_stored', 0):,} (RAM 캐시 {result.cell_counts.get('detail_cache_cells', 0):,} Cell)",
                    "AI 판단(컬럼)": str(result.ai_audit.get("attempts", 0)),
                    "AI 판단(Row)": str(result.ai_audit.get("row", {}).get("attempts", 0)),
                    "탐지된 개인정보 유형": len({r.privacy_type for r in result.results}),
                    "발견된 컬럼 수": len({r.column for r in result.results}),
                    "현재 분석 컬럼": max(result.results, key=lambda r: r.hits).column if result.results else "-",
                    "메모리 사용량": f"{engine.memory_usage()['rss_mb']:.1f} MB",
                    "AI 실행 상태": result.ai_summary.get("status", "-"),
                    "추론 방식": result.ai_summary.get("backend", "-"),
                    "AI 모델": result.ai_summary.get("model") or "없음",
                    "AI 판정 수": str(result.ai_summary.get("decisions", 0)),
                    "AI 생략/오류": result.ai_summary.get("skipped_reason") or result.ai_summary.get("error") or "-",
                }
            )
        output_dir = Path("results")
        output_dir.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        write_csv([r.as_dict() for result in results for r in result.results],
                  output_dir / f"privacy_results_{stamp}.csv")
        write_txt(results, output_dir / f"privacy_report_{stamp}.txt")
        write_hit_rows(results, output_dir / f"privacy_hits_{stamp}.csv")
        write_row_records(results, output_dir / f"privacy_rows_{stamp}.csv")
        self.result_window.show_results([r for result in results for r in result.results])
        self.result_window.show_rows(results)
        self.diagnostics = diagnostics.events
        if config.debug:
            self.debug_label.setText("\n".join(
                f"[{e['ts']}s] {e['event']}: "
                + ", ".join(f"{k}={v}" for k, v in e.items() if k not in {"event", "ts"})
                for e in self.diagnostics[:50]
            ))
        else:
            self.debug_label.setText("디버그 정보 없음 (환경 설정에서 디버그 로그를 활성화하세요).")


def run_gui(args=None) -> int:
    import sys

    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()
