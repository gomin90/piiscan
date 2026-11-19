"""결과 테이블 및 컬럼 상세 보기."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QGridLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

COLUMNS = [
    "파일명",
    "원본DB",
    "스키마",
    "테이블명",
    "컬럼명",
    "개인정보 유형",
    "탐지 건수",
    "분석 데이터 건수",
    "탐지 비율",
    "보호 컬럼",
    "분석 방식",
    "신뢰도",
    "위험도",
    "판단 근거",
]

RISK_ORDER = {"매우 높음": 0, "높음": 1, "보통": 2, "낮음": 3, "확인 필요": 4}


class ResultWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        self.summary = QLabel("분석 결과 없음")
        layout.addWidget(self.summary)
        self.table = QTableWidget()
        self.table.setColumnCount(len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        layout.addWidget(self.table)
        self.detail = QWidget()
        detail_layout = QGridLayout(self.detail)
        self.detail_labels: dict[str, QLabel] = {}
        for index, field in enumerate(
            ["파일", "테이블", "컬럼", "유형", "탐지 건수", "탐지 비율", "보호 상태", "규칙 근거", "AI근거", "위험도"]
        ):
            detail_layout.addWidget(QLabel(field), index, 0)
            self.detail_labels[field] = QLabel("-")
            detail_layout.addWidget(self.detail_labels[field], index, 1)
        layout.addWidget(self.detail)

    def show_results(self, results: list) -> None:
        rows = sorted(results, key=lambda r: (RISK_ORDER.get(r.risk_level, 9), -r.hits))
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            values = [
                row.file,
                row.db_type or "-",
                row.schema or "-",
                row.table,
                row.column,
                row.display_name,
                str(row.hits),
                str(row.analysed_rows),
                f"{row.ratio:.2%}",
                ", ".join(row.protection_columns) or "-",
                row.method,
                f"{row.rule_confidence:.2f} / {row.ai_confidence:.2f}",
                row.risk_level,
                row.rationale[:160],
            ]
            for column, value in enumerate(values):
                self.table.setItem(index, column, QTableWidgetItem(value))
        high = sum(1 for row in rows if row.risk_level in ("매우 높음", "높음"))
        self.summary.setText(f"총 {len(rows)}건 / 위험도 높음 {high}건")

    def show_detail(self, column: str) -> None:
        for field, label in self.detail_labels.items():
            label.setText("-")
        self.detail_labels["컬럼"].setText(column)
