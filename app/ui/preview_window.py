"""CSV 미리보기: 20행 + 형식 설정 수정. 컬럼 샘플은 마스킹되어 표시된다."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.csv_detector import detect_format, read_preview
from app.utils.masking import mask_value

PREVIEW_ROWS = 20


class PreviewWindow(QWidget):
    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path
        self.profile = detect_format(path)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"{path.name} - 형식 자동 판단 (신뢰도 {self.profile.confidence:.2f})"))

        controls = QGridLayout()
        self.encoding_box = QComboBox()
        self.encoding_box.addItems(["utf-8", "utf-8-sig", "cp949", "euc-kr", "utf-16", "utf-16-le", "utf-16-be"])
        self.encoding_box.setCurrentText(self.profile.encoding)
        self.delimiter_box = QComboBox()
        self.delimiter_box.addItems([",", ";", "\t", "|"])
        self.delimiter_box.setCurrentText(self.profile.delimiter)
        self.header_box = QComboBox()
        self.header_box.addItems(["헤더 없음", "헤더 있음"])
        self.header_box.setCurrentIndex(1 if self.profile.has_header else 0)
        self.start_row_box = QSpinBox()
        self.start_row_box.setRange(0, 50)
        self.table_name_box = QLabel(self.profile.path.split("/")[-1])

        controls.addWidget(QLabel("인코딩"), 0, 0)
        controls.addWidget(self.encoding_box, 0, 1)
        controls.addWidget(QLabel("구분자"), 0, 2)
        controls.addWidget(self.delimiter_box, 0, 3)
        controls.addWidget(QLabel("헤더"), 0, 4)
        controls.addWidget(self.header_box, 0, 5)
        controls.addWidget(QLabel("데이터 시작 행"), 1, 0)
        controls.addWidget(self.start_row_box, 1, 1)
        controls.addWidget(QLabel("테이블명"), 1, 2)
        controls.addWidget(self.table_name_box, 1, 3)
        layout.addLayout(controls)

        self.table = QTableWidget()
        layout.addWidget(self.table)
        layout.addWidget(QLabel("샘플 값은 개인정보 보호를 위해 마스킹되어 표시됩니다."))
        self.refresh()

    def settings(self) -> dict:
        delimiter = self.delimiter_box.currentText()
        return {
            "encoding": self.encoding_box.currentText(),
            "delimiter": delimiter,
            "has_header": self.header_box.currentIndex() == 1,
            "header_start_row": self.start_row_box.value(),
            "table": self.table_name_box.text(),
        }

    def refresh(self) -> None:
        settings = self.settings()
        rows = read_preview(self.path, settings["encoding"], settings["delimiter"], PREVIEW_ROWS)
        self.table.clear()
        if not rows:
            return
        width = max(len(row) for row in rows)
        self.table.setColumnCount(width)
        if settings["has_header"]:
            self.table.setHorizontalHeaderLabels(rows[0])
            data_rows = rows[1:]
        else:
            data_rows = rows
        self.table.setRowCount(len(data_rows))
        for r, row in enumerate(data_rows):
            for c, value in enumerate(row):
                self.table.setItem(r, c, QTableWidgetItem(mask_value(value)))
