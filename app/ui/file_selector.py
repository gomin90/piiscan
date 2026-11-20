"""파일 선택 위젯: 다중 파일 / 폴더 선택 + 형식 정보."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.core.csv_detector import detect_format


class FileSelector(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.files: list[Path] = []
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.file_button = QPushButton("CSV 파일 선택")
        self.folder_button = QPushButton("폴더 선택")
        # Without these connections the buttons are inert and no file can be
        # picked from the GUI.
        self.file_button.clicked.connect(self.choose_files)
        self.folder_button.clicked.connect(self.choose_folder)
        row.addWidget(self.file_button)
        row.addWidget(self.folder_button)
        layout.addLayout(row)
        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget)
        self.info_label = QLabel("파일을 선택하면 인코딩/구분자/헤더 정보가 표시된다.")
        layout.addWidget(self.info_label)
        self.list_widget.currentItemChanged.connect(self._show_info)

    def choose_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "CSV 파일 선택", "", "CSV (*.csv *.txt)")
        if not paths:
            return
        self.files = [Path(path) for path in paths]
        self.list_widget.clear()
        for path in self.files:
            self.list_widget.addItem(self._describe(path))
        self.info_label.setText(f"{len(self.files)}개 파일 선택됨")

    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "폴더 선택")
        if not folder:
            return
        base = Path(folder)
        self.files = sorted({p for pattern in ("*.csv", "*.txt") for p in base.glob(pattern)})
        self.list_widget.clear()
        for path in self.files:
            self.list_widget.addItem(self._describe(path))
        self.info_label.setText(
            f"{len(self.files)}개 파일 선택됨" if self.files else "선택한 폴더에 CSV/TXT 파일 없음"
        )

    @staticmethod
    def _describe(path: Path) -> str:
        profile = detect_format(path)
        delimiter = "TAB" if profile.delimiter == "\t" else profile.delimiter
        return (
            f"{path.name} | {profile.encoding} | 구분자 {delimiter} | "
            f"헤더 {'Yes' if profile.has_header else 'No'} | "
            f"컬럼 {profile.column_count} | 크기 {path.stat().st_size // 1024} KB"
        )

    def _show_info(self, item, _previous=None) -> None:
        if item:
            self.info_label.setText(item.text())
