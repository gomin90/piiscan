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

from app.ai.gpu_detector import detect_hardware
from app.ai.llm_manager import LocalLLMEngine
from app.config.settings import AnalysisConfig
from app.core.analysis_service import analyse_file
from app.export.csv_exporter import write_csv
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
        self.setWindowTitle("개인정보 탐지 및 분석 시스템 (Privacy Scanner)")
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
        for key, value in self.hardware.as_dict().items():
            hardware_layout.addWidget(QLabel(f"{key}: {value}"))
        tabs.addTab(hardware_widget, "하드웨어 정보")

        self.settings_window = SettingsWindow(self.config)
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
        engine = LocalLLMEngine(config, self.hardware)
        results = []
        for index, path in enumerate(self.selector.files):
            self.progress.update_state(
                **{
                    "현재 분석 파일": path.name,
                    "전체 파일 수": len(self.selector.files),
                }
            )
            result = analyse_file(path, config, engine, diagnostics=diagnostics,
                                  progress=lambda done, total: self.progress.update_state(
                                      **{"처리 행 수": done, "전체 행 수": total}
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
                    "탐지된 개인정보 유형": len({r.privacy_type for r in result.results}),
                    "발견된 컬럼 수": len({r.column for r in result.results}),
                    "AI 실행 상태": result.ai_summary.get("status", "-"),
                    "추론 방식": result.ai_summary.get("backend", "-"),
                }
            )
        output_dir = Path("results")
        output_dir.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        write_csv([r.as_dict() for result in results for r in result.results],
                  output_dir / f"privacy_results_{stamp}.csv")
        write_txt(results, output_dir / f"privacy_report_{stamp}.txt")
        self.result_window.show_results([r for result in results for r in result.results])
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
