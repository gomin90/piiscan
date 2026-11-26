"""분석 진행 표시: 행 수, 진행률, 메모리, AI 상태, CPU/GPU 모드."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QProgressBar,
    QGridLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from app.ai.gpu_detector import HardwareReport


class AnalysisWindow(QWidget):
    def __init__(self, hardware: HardwareReport) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)

        grid = QGridLayout()
        self.labels: dict[str, QLabel] = {}
        fields = [
            "현재 분석 파일",
            "전체 파일 수",
            "파일 진행",
            "분석 상태",
            "분석 오류",
            "처리 행 수",
            "전체 행 수",
            "처리 Cell 수",
            "현재 단계",
            "진행률",
            "경과 시간",
            "예상 잔여 시간(ETA)",
            "처리 속도",
            "분석 범위",
            "AI 후보",
            "AI 추론 횟수",
            "AI batch",
            "AI Reuse",
            "AI 성공/실패/불가",
            "Human Review 대기",
            "Export 진행",
            "AI 진행률",
            "개인정보 Row 수",
            "Row 상세 저장",
            "Cell 상세 저장",
            "Cell 판정 저장",
            "AI 판단(컬럼)",
            "AI 판단(Cell)",
            "AI 판단(Row)",
            "현재 분석 컬럼",
            "탐지된 개인정보 유형",
            "발견된 컬럼 수",
            "메모리 사용량",
            "AI 실행 상태",
            "추론 방식",
            "AI 모델",
            "AI 판정 수",
            "AI 생략/오류",
        ]
        for index, field in enumerate(fields):
            grid.addWidget(QLabel(field), index, 0)
            self.labels[field] = QLabel("-")
            grid.addWidget(self.labels[field], index, 1)
        layout.addLayout(grid)
        layout.addWidget(QLabel(f"CPU: {hardware.cpu_model} | RAM {hardware.total_ram_gb:.1f}GB | GPU: {hardware.gpu_model or '없음'}"))

    def update_state(self, **values) -> None:
        for key, value in values.items():
            if key in self.labels:
                self.labels[key].setText(str(value))
        percent = values.get("progress_percent")
        if percent is not None:
            # None means the denominator is genuinely unknown: keep the bar at its
            # previous value rather than inventing a percentage.
            self.progress.setValue(int(percent))
