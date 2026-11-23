"""결과 테이블 및 Row/Cell 단위 상세 보기."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.config.settings import AnalysisConfig

COLUMNS = [
    "파일명",
    "원본DB",
    "스키마",
    "테이블명",
    "컬럼명",
    "컬럼 인덱스",
    "개인정보 유형",
    "판정",
    "탐지 건수",
    "분석 데이터 건수",
    "탐지 비율",
    "보호 컬럼",
    "규칙 판정",
    "AI 판정",
    "규칙/AI 일치",
    "분석 방식",
    "신뢰도 (규칙/AI)",
    "위험도",
    "판단 근거",
]

RISK_ORDER = {"매우 높음": 0, "높음": 1, "보통": 2, "낮음": 3, "확인 필요": 4}


class ResultWindow(QWidget):
    """Column findings plus paginated Row/Cell detail.

    Cell detail is rendered only for the Row the user selects, at most
    `gui_cell_page_size` cells per page, so a 10,000-column Row cannot fill the
    widget with ten million items. The full detail always stays in SQLite.
    """

    def __init__(self, config: AnalysisConfig | None = None) -> None:
        super().__init__()
        self.config = config or AnalysisConfig()
        self.cell_page_size = max(1, self.config.gui_cell_page_size)
        layout = QVBoxLayout(self)
        self.summary = QLabel("분석 결과 없음")
        layout.addWidget(self.summary)
        self.table = QTableWidget()
        self.table.setColumnCount(len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.horizontalHeader().setStretchLastSection(True)
        widths = [90, 60, 60, 90, 120, 60, 110, 100, 70, 90, 70, 120, 90, 90, 70, 80, 90, 80, 220]
        for index, width in enumerate(widths):
            self.table.setColumnWidth(index, width)
        self.table.cellClicked.connect(self._on_row_clicked)
        self.rows: list = []
        self._file_results: list = []
        self._page = 0
        self._page_size = 200
        self._row_page: list[dict] = []
        self._selected_row: dict | None = None
        self._cell_page = 0
        layout.addWidget(self.table)
        self.detail = QWidget()
        detail_layout = QGridLayout(self.detail)
        self.detail_labels: dict[str, QLabel] = {}
        for index, field in enumerate(
            ["파일", "테이블", "컬럼", "유형", "탐지 건수", "탐지 비율", "보호 상태",
             "규칙 판정", "AI 판정", "규칙/AI 일치", "최종 판정", "규칙 근거", "AI근거", "위험도", "추가 검토"]
        ):
            detail_layout.addWidget(QLabel(field), index, 0)
            self.detail_labels[field] = QLabel("-")
            detail_layout.addWidget(self.detail_labels[field], index, 1)
        layout.addWidget(self.detail)

        # Row/cell level evidence: exactly which source cells matched.
        layout.addWidget(QLabel("Row/Cell 단위 상세 (값은 마스킹됨)"))
        self.hit_table = QTableWidget()
        self.hit_table.setColumnCount(6)
        self.hit_table.setHorizontalHeaderLabels(
            ["행번호", "컬럼", "컬럼 인덱스", "유형", "Cell 판정", "탐지값(마스킹)"]
        )
        for index, width in enumerate([70, 120, 70, 110, 110, 160]):
            self.hit_table.setColumnWidth(index, width)
        layout.addWidget(self.hit_table)

        self.row_table = QTableWidget()
        self.row_table.setColumnCount(7)
        self.row_table.setHorizontalHeaderLabels(
            ["행번호", "개인정보 상태", "확정 개인정보", "검토 필요", "발생 컬럼",
             "Row 위험도", "AI Row 판정"]
        )
        for index, width in enumerate([70, 90, 130, 130, 150, 90, 110]):
            self.row_table.setColumnWidth(index, width)
        layout.addWidget(QLabel("Row 판정 목록 (Row를 선택하면 그 Row의 Cell 상세)"))
        self.row_table.cellClicked.connect(self._on_result_row_clicked)
        layout.addWidget(self.row_table)
        self.row_context = QLabel("Row 상세 없음")
        layout.addWidget(self.row_context)
        page_row = QHBoxLayout()
        self.prev_page = QPushButton("이전 Row 페이지")
        self.next_page = QPushButton("다음 Row 페이지")
        self.prev_page.clicked.connect(lambda: self._change_page(-1))
        self.next_page.clicked.connect(lambda: self._change_page(1))
        page_row.addWidget(self.prev_page)
        page_row.addWidget(self.next_page)
        layout.addLayout(page_row)

        # Cell detail is rendered only for the Row the user selects, and only
        # `cell_page_size` cells at a time, so a 10,000-column Row cannot flood
        # the widget. The complete cell list always stays queryable in SQLite.
        layout.addWidget(QLabel("선택한 Row의 Cell 상세"))
        self.cell_label = QLabel("Row를 선택하면 Cell 상세")
        layout.addWidget(self.cell_label)
        cell_row = QHBoxLayout()
        self.prev_cell_page = QPushButton("이전 Cell 페이지")
        self.next_cell_page = QPushButton("다음 Cell 페이지")
        self.prev_cell_page.clicked.connect(lambda: self._change_cell_page(-1))
        self.next_cell_page.clicked.connect(lambda: self._change_cell_page(1))
        self.prev_cell_page.setEnabled(False)
        self.next_cell_page.setEnabled(False)
        cell_row.addWidget(self.prev_cell_page)
        cell_row.addWidget(self.next_cell_page)
        layout.addLayout(cell_row)

    def show_hits(self, result) -> None:
        rows = result.hit_rows if hasattr(result, "hit_rows") else []
        self.hit_table.setRowCount(len(rows))
        for index, hit in enumerate(rows):
            self.hit_table.setItem(index, 0, QTableWidgetItem(str(hit["row"])))
            self.hit_table.setItem(index, 1, QTableWidgetItem(result.column))
            self.hit_table.setItem(index, 2, QTableWidgetItem(str(getattr(result, "column_index", 0))))
            self.hit_table.setItem(index, 3, QTableWidgetItem(result.display_name))
            self.hit_table.setItem(index, 4, QTableWidgetItem(getattr(result, "verdict", "개인정보")))
            self.hit_table.setItem(index, 5, QTableWidgetItem(hit["value"]))

    def show_rows(self, file_results: list, page: int = 0, page_size: int = 200) -> None:
        """Render one page of row verdicts (and its cell detail) from the detail store."""
        self._file_results = file_results
        self._page_size = page_size
        self._page = page
        total = 0
        for result in file_results:
            store = getattr(result, "detail_store", None)
            total += store.row_count if store is not None else len(getattr(result, "row_records", []))
        offset = page * page_size
        records = list(self._page_records(file_results, offset, page_size))
        self.row_table.setRowCount(len(records))
        for index, record in enumerate(records):
            self.row_table.setItem(index, 0, QTableWidgetItem(str(record["row"])))
            self.row_table.setItem(index, 1, QTableWidgetItem(record["status"]))
            self.row_table.setItem(index, 2, QTableWidgetItem(", ".join(record["confirmed_pii"])))
            self.row_table.setItem(index, 3, QTableWidgetItem(", ".join(record["possible_pii"])))
            self.row_table.setItem(index, 4, QTableWidgetItem(", ".join(record["column_names"])))
            self.row_table.setItem(index, 5, QTableWidgetItem(record["risk_level"]))
            ai = record.get("ai_review") or {}
            self.row_table.setItem(index, 6, QTableWidgetItem(ai.get("personality", "-") or "-"))

        self._row_page = records
        self._selected_row = None
        self._cell_page = 0
        self.hit_table.setRowCount(0)
        self.cell_label.setText("Cell 상세: Row를 선택하면 그 Row의 Cell 상세 (전량 SQLite)")

        last_page = max(0, (total - 1) // page_size)
        self.row_context.setText(
            f"Row 상세 {total:,}건 저장됨 (SQLite) - 페이지 {page + 1}/{last_page + 1}, "
            f"이 페이지 {len(records)}건"
        )
        self.prev_page.setEnabled(page > 0)
        self.next_page.setEnabled(page < last_page)

    def _page_records(self, file_results: list, offset: int, limit: int):
        remaining = offset
        for result in file_results:
            store = getattr(result, "detail_store", None)
            count = store.row_count if store is not None else len(getattr(result, "row_records", []))
            if remaining >= count:
                remaining -= count
                continue
            take = min(limit, count - remaining)
            source = store.iter_rows(remaining, take) if store is not None else [
                record.as_dict() for record in result.row_records[remaining:remaining + take]
            ]
            for record in source:
                record["_store"] = store
                yield record
            limit -= take
            remaining = 0
            if limit <= 0:
                break

    def _change_page(self, delta: int) -> None:
        self.show_rows(self._file_results, self._page + delta, self._page_size)

    def _on_result_row_clicked(self, index: int, _column: int) -> None:
        if 0 <= index < len(self._row_page):
            self.select_row(self._row_page[index])

    def select_row(self, record: dict) -> None:
        """Load one Row's cell detail from SQLite and render one page of it."""
        self._selected_row = record
        self._cell_page = 0
        self._render_cell_page()

    def _cell_total(self) -> int:
        record = self._selected_row
        store = record.get("_store")
        if store is None:
            return record.get("cell_count", 0)
        return store.row_cell_count(record["row"])

    def _render_cell_page(self) -> None:
        record = self._selected_row
        if record is None:
            return
        total = self._cell_total()
        page_size = self.cell_page_size
        last_page = max(0, (total - 1) // page_size)
        self._cell_page = min(self._cell_page, last_page)
        offset = self._cell_page * page_size
        store = record.get("_store")
        if store is not None:
            cells = list(store.cells_for_row(record["row"], offset=offset, limit=page_size))
        else:
            cells = [cell.as_dict() for cell in record.get("_cells", [])][offset:offset + page_size]
        self.hit_table.setRowCount(len(cells))
        for index, cell in enumerate(cells):
            self.hit_table.setItem(index, 0, QTableWidgetItem(str(cell["row"])))
            self.hit_table.setItem(index, 1, QTableWidgetItem(cell["column"]))
            self.hit_table.setItem(index, 2, QTableWidgetItem(str(cell["column_index"])))
            self.hit_table.setItem(index, 3, QTableWidgetItem(cell["display_name"]))
            self.hit_table.setItem(index, 4, QTableWidgetItem(cell["decision"]))
            self.hit_table.setItem(index, 5, QTableWidgetItem(cell["masked_value"]))
        self.cell_label.setText(
            f"Row {record['row']} Cell 상세 {total:,}건 (SQLite) - "
            f"Cell 페이지 {self._cell_page + 1}/{last_page + 1}, 이 페이지 {len(cells)}건 "
            f"(화면 상한 {page_size})"
        )
        self.prev_cell_page.setEnabled(self._cell_page > 0)
        self.next_cell_page.setEnabled(self._cell_page < last_page)

    def _change_cell_page(self, delta: int) -> None:
        self._cell_page += delta
        self._render_cell_page()

    def show_results(self, results: list) -> None:
        rows = sorted(results, key=lambda r: (RISK_ORDER.get(r.risk_level, 9), -r.hits))
        self.rows = rows
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            values = [
                row.file,
                row.db_type or "-",
                row.schema or "-",
                row.table,
                row.column,
                str(getattr(row, "column_index", 0)),
                row.display_name,
                getattr(row, "verdict", "개인정보"),
                str(row.hits),
                str(row.analysed_rows),
                f"{row.ratio:.2%}",
                ", ".join(row.protection_columns) or "-",
                getattr(row, "rule_decision", "") or "-",
                getattr(row, "ai_decision", "") or "-",
                getattr(row, "agreement", "AI 미사용"),
                row.method,
                f"{row.rule_confidence:.2f} / {row.ai_confidence:.2f}",
                row.risk_level,
                row.rationale[:160],
            ]
            for column, value in enumerate(values):
                self.table.setItem(index, column, QTableWidgetItem(value))
        high = sum(1 for row in rows if row.risk_level in ("매우 높음", "높음"))
        self.summary.setText(f"총 {len(rows)}건 / 위험도 높음 {high}건")

    def _on_row_clicked(self, row: int, _column: int) -> None:
        if 0 <= row < len(self.rows):
            self.show_detail(self.rows[row])

    def show_detail(self, result) -> None:
        self.show_hits(result)
        protection = ", ".join(result.protection_columns) or "없음"
        values = {
            "파일": result.file,
            "테이블": result.table,
            "컬럼": result.column,
            "유형": result.display_name,
            "탐지 건수": f"{result.hits} / {result.analysed_rows}행",
            "탐지 비율": f"{result.ratio:.2%}",
            "보호 상태": f"{protection} | MASK {result.mask_status} | HASH {result.hash_status}",
            "규칙 판정": result.rule_decision or "없음",
            "AI 판정": result.ai_decision or "없음",
            "규칙/AI 일치": result.agreement,
            "최종 판정": result.final_decision or result.privacy_type,
            "규칙 근거": result.rationale or "-",
            "AI근거": (
                f"AI 신뢰도 {result.ai_confidence:.2f} (규칙 {result.rule_confidence:.2f})"
                if result.ai_confidence else "AI 미사용/미확정"
            ),
            "위험도": result.risk_level,
            "추가 검토": "예" if result.requires_review else "아니오",
        }
        for field, label in self.detail_labels.items():
            label.setText(values.get(field, "-"))
