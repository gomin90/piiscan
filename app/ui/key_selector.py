"""KEY column selection for Function A.

KEY columns are chosen by the user, never inferred: a table's primary key is a
record identifier, not a person. The widget lists every header of the selected
file with a checkbox so a single KEY, a composite KEY, or no KEY at all can be
declared explicitly.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.csv_detector import detect_format


class KeySelector(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        self.label = QLabel("KEY 컬럼을 직접 선택하세요 (자동推断 하지 않습니다). "
                            "KEY 컬럼은 개인정보 검사에서 제외되고 레코드 식별용으로 사용됩니다.")
        layout.addWidget(self.label)
        self.load_button = QPushButton("컬럼 목록 불러오기")
        self.load_button.clicked.connect(self.load_columns)
        layout.addWidget(self.load_button)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["KEY", "컬럼명", " 인덱스"])
        layout.addWidget(self.table)
        self.summary = QLabel("KEY 없음 - 모든 컬럼이 개인정보 검사 대상")
        layout.addWidget(self.summary)
        self.files: list[Path] = []

    def set_files(self, files) -> None:
        self.files = [Path(f) for f in files]

    def load_columns(self) -> list[str]:
        if not self.files:
            self.label.setText(" 먼저 분석 파일을 선택하세요")
            return []
        profile = detect_format(self.files[0])
        names = profile.column_names or []
        self.table.clear()
        self.table.setRowCount(len(names))
        for index, name in enumerate(names):
            checkbox = QCheckBox()
            checkbox.stateChanged.connect(self._update_summary)
            self.table.setCellWidget(index, 0, checkbox)
            self.table.setItem(index, 1, QTableWidgetItem(name))
            self.table.setItem(index, 2, QTableWidgetItem(str(index)))
        self._update_summary()
        return names

    def selected_keys(self) -> list[str]:
        keys = []
        for index in range(self.table.rowCount()):
            checkbox = self.table.cellWidget(index, 0)
            if checkbox is not None and checkbox.isChecked():
                item = self.table.item(index, 1)
                if item:
                    keys.append(item.text())
        return keys

    def set_keys(self, names: list[str]) -> None:
        wanted = {name.strip().lower() for name in names}
        for index in range(self.table.rowCount()):
            item = self.table.item(index, 1)
            checkbox = self.table.cellWidget(index, 0)
            if item and checkbox and item.text().strip().lower() in wanted:
                checkbox.setChecked(True)
        self._update_summary()

    def _update_summary(self) -> None:
        keys = self.selected_keys()
        if keys:
            self.summary.setText(f"KEY: {', '.join(keys)} ({len(keys)} 컬럼) - 분석 Cell에서 제외")
        else:
            self.summary.setText("KEY 없음 - 모든 컬럼이 개인정보 검사 대상")

    def key_count(self) -> int:
        return len(self.selected_keys())
