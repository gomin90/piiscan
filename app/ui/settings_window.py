"""환경 설정 및 디버그 정보 pane."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.config.settings import AnalysisConfig


class SettingsWindow(QWidget):
    def __init__(self, config: AnalysisConfig) -> None:
        super().__init__()
        self.config = config
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.batch_box = QSpinBox()
        self.batch_box.setRange(100, 10000)
        self.batch_box.setValue(config.batch_size)
        self.context_box = QSpinBox()
        self.context_box.setRange(512, 8192)
        self.context_box.setValue(config.context_length)
        self.thread_box = QSpinBox()
        self.thread_box.setRange(1, 8)
        self.thread_box.setValue(config.threads)
        self.ai_check = QCheckBox("Local LLM 보조 판단 사용")
        self.ai_check.setChecked(config.use_ai)
        self.debug_check = QCheckBox("디버그 진단 로그")
        self.debug_check.setChecked(config.debug)
        form.addRow("배치 크기 (행)", self.batch_box)
        form.addRow("Context length", self.context_box)
        form.addRow("CPU 스레드", self.thread_box)
        form.addRow("AI", self.ai_check)
        form.addRow("Debug", self.debug_check)
        layout.addLayout(form)
        self.model_label = QLabel("GGUF 모델: models/ 디렉터리에 포함된 모델만 사용 (다운로드 없음)")
        layout.addWidget(self.model_label)

    def apply(self) -> AnalysisConfig:
        self.config.batch_size = self.batch_box.value()
        self.config.context_length = self.context_box.value()
        self.config.threads = self.thread_box.value()
        self.config.use_ai = self.ai_check.isChecked()
        self.config.debug = self.debug_check.isChecked()
        return self.config
