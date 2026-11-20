"""환경 설정 및 AI 판정 engine 상태 pane.

CLI의 --mode/--table/--schema/--db 옵션이 GUI에도 그대로 노출된다 (양식).
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.ai.jev.model_manager import JevStyleModelManager
from app.config.settings import AnalysisConfig


class SettingsWindow(QWidget):
    def __init__(self, config: AnalysisConfig, hardware=None) -> None:
        super().__init__()
        self.config = config
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.mode_box = QComboBox()
        self.mode_box.addItems(["auto", "sampling", "full"])
        self.mode_box.setCurrentText(config.mode)
        self.table_box = QLineEdit(config.table)
        self.table_box.setPlaceholderText("예: 고객테이블 (비워두면 파일명)")
        self.schema_box = QLineEdit(config.schema)
        self.schema_box.setPlaceholderText("예: PROD")
        self.db_box = QLineEdit(config.db_type)
        self.db_box.setPlaceholderText("예: Oracle, MySQL, MSSQL")
        self.batch_box = QSpinBox()
        self.batch_box.setRange(100, 10000)
        self.batch_box.setValue(config.batch_size)
        self.context_box = QSpinBox()
        self.context_box.setRange(512, 8192)
        self.context_box.setValue(config.context_length)
        self.hit_rows_box = QSpinBox()
        self.hit_rows_box.setRange(0, 500)
        self.hit_rows_box.setValue(config.max_hit_rows)
        self.thread_box = QSpinBox()
        self.thread_box.setRange(1, 8)
        self.thread_box.setValue(config.threads)
        self.ai_check = QCheckBox("AI 보조 판정 사용")
        self.ai_check.setChecked(config.use_ai)
        self.engine_box = QComboBox()
        self.engine_box.addItems(["auto", "jev", "llm"])
        self.engine_box.setCurrentText(config.ai_engine)
        self.model_box = QComboBox()
        self.model_box.addItems(["auto", "0.8b-v3", "2b-v4"])
        self.model_box.setCurrentText(config.jev_model)
        self.debug_check = QCheckBox("디버그 진단 로그")
        self.debug_check.setChecked(config.debug)
        form.addRow("분석 방식", self.mode_box)
        form.addRow("테이블명", self.table_box)
        form.addRow("스키마", self.schema_box)
        form.addRow("원본 DB", self.db_box)
        form.addRow("배치 크기 (행)", self.batch_box)
        form.addRow("Context length", self.context_box)
        form.addRow("행 상세 기록 (0 = 없음)", self.hit_rows_box)
        form.addRow("CPU 스레드", self.thread_box)
        form.addRow("AI", self.ai_check)
        form.addRow("AI engine", self.engine_box)
        form.addRow("Jev-Style 모델", self.model_box)
        form.addRow("Debug", self.debug_check)
        layout.addLayout(form)

        layout.addWidget(QLabel("GGUF 모델: models/ 디렉터리에 포함된 모델만 사용 (다운로드 없음)"))
        self.ai_status = QLabel("AI 상태: 분석 시작 시 계산")
        layout.addWidget(self.ai_status)
        self.refresh_ai_status(hardware)

    def refresh_ai_status(self, hardware=None) -> None:
        specs = JevStyleModelManager().discover()
        if not specs:
            self.ai_status.setText(
                "Jev-Style 모델 없음 -> 규칙 기반 분석 (models/jev-style/<release>/ 에 GGUF + scorer 필요)"
            )
            return
        lines = ["Jev-Style 모델 상태:"]
        for spec in specs:
            state = "무결성 OK" if spec.verified else "무결성 실패: " + "; ".join(spec.problems)
            scorer = spec.scorer_path.name if spec.scorer_path else "scorer 없음"
            lines.append(f"  {spec.model_name} ({spec.quant}, {spec.size_bytes / (1024 ** 3):.2f}GB, {scorer}) - {state}")
        if hardware is not None:
            lines.append(f"  사용 가능한 RAM: {hardware.available_ram_gb:.1f}GB / backend: {hardware.backend}")
            if hardware.gpu_model:
                gpu_line = f"  GPU: {hardware.gpu_vendor} {hardware.gpu_model} (VRAM {hardware.vram_total_gb:.1f}GB)"
                gpu_line += " -> GPU 추론 가능" if hardware.gpu_backend_ready else " -> GPU backend DLL 없음, CPU 추론"
                lines.append(gpu_line)
            for note in hardware.notes:
                lines.append(f"  {note}")
        self.ai_status.setText("\n".join(lines))

    def apply(self) -> AnalysisConfig:
        self.config.mode = self.mode_box.currentText()
        self.config.table = self.table_box.text().strip()
        self.config.schema = self.schema_box.text().strip()
        self.config.db_type = self.db_box.text().strip()
        self.config.batch_size = self.batch_box.value()
        self.config.context_length = self.context_box.value()
        self.config.max_hit_rows = self.hit_rows_box.value()
        self.config.threads = self.thread_box.value()
        self.config.use_ai = self.ai_check.isChecked()
        self.config.ai_engine = self.engine_box.currentText()
        self.config.jev_model = self.model_box.currentText()
        self.config.debug = self.debug_check.isChecked()
        return self.config
