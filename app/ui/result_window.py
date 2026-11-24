"""결과 테이블 및 Row/Cell 단위 상세 보기."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTableView,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.config.settings import AnalysisConfig
from app.core.cell_verdict import STATUSES, STATUS_KEY
from app.ui.table_model import PagedTableModel

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

ROW_HEADERS = ["행번호", "개인정보 상태", "확정 개인정보", "검토 필요", "Cell(PII)",
              "Cell(검토)", "Cell(미평가)", "Cell(비PII)", "Cell(빈)", "KEY 제외", "Row 위험도",
              "AI Row 판정"]
CELL_HEADERS = ["행번호", "컬럼", "컬럼 인덱스", "Cell 판정", "개인정보 유형", "규칙 판정",
                "AI 판정", "최종 판정", "신뢰도", "판단 방법", "검토", "마스킹 값"]

RISK_ORDER = {"매우 높음": 0, "높음": 1, "보통": 2, "낮음": 3, "확인 필요": 4}


class ResultWindow(QWidget):
    """Column findings plus paginated Row/Cell detail.

    Row and Cell lists are `QTableView`s backed by models that read one page from
    SQLite at a time. Nothing is loaded in bulk: selecting a Row fetches only that
    Row's cells, and those are themselves paginated, so a 10,000-column Row does
    not put ten million widgets on screen.
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

        views = QTabWidget()

        # Row verdicts: one page at a time straight from SQLite.
        self.row_view = QTableView()
        self.row_model = PagedTableModel(ROW_HEADERS)
        self.row_view.setModel(self.row_model)
        self.row_view.horizontalHeader().setStretchLastSection(True)
        self.row_view.clicked.connect(lambda index: self._on_result_row_clicked(index.row()))
        views.addTab(self.row_view, "Row 판정")

        # Complete Cell ledger: every analysed cell, matched or not.
        self.cell_view = QTableView()
        self.cell_model = PagedTableModel(CELL_HEADERS)
        self.cell_view.setModel(self.cell_model)
        self.cell_view.horizontalHeader().setStretchLastSection(True)
        cell_bar = QHBoxLayout()
        self.status_filter = QComboBox()
        self.status_filter.addItem("전체")
        for status in STATUSES:
            self.status_filter.addItem(status)
        self.prev_cell_page = QPushButton("이전 Cell 페이지")
        self.next_cell_page = QPushButton("다음 Cell 페이지")
        self.prev_cell_page.setEnabled(False)
        self.next_cell_page.setEnabled(False)
        cell_bar.addWidget(QLabel("판정filter"))
        cell_bar.addWidget(self.status_filter)
        cell_bar.addWidget(self.prev_cell_page)
        cell_bar.addWidget(self.next_cell_page)
        cell_panel = QWidget()
        cell_panel_layout = QVBoxLayout(cell_panel)
        self.cell_context = QLabel("Cell 판정 목록 없음")
        cell_panel_layout.addWidget(self.cell_context)
        cell_panel_layout.addLayout(cell_bar)
        cell_panel_layout.addWidget(self.cell_view)
        views.addTab(cell_panel, "Cell 판정 (전체)")

        # Selected Row's own cells, including the masked value and evidence.
        self.hit_view = QTableView()
        self.hit_model = PagedTableModel(
            ["행번호", "컬럼", "컬럼 인덱스", "유형", "Cell 판정", "탐지값(마스킹)"]
        )
        self.hit_view.setModel(self.hit_model)
        self.hit_view.horizontalHeader().setStretchLastSection(True)
        hit_panel = QWidget()
        hit_layout = QVBoxLayout(hit_panel)
        self.cell_label = QLabel("Row를 선택하면 Cell 상세")
        hit_row = QHBoxLayout()
        self.prev_hit_page = QPushButton("이전 Cell 페이지")
        self.next_hit_page = QPushButton("다음 Cell 페이지")
        self.prev_hit_page.setEnabled(False)
        self.next_hit_page.setEnabled(False)
        hit_row.addWidget(self.prev_hit_page)
        hit_row.addWidget(self.next_hit_page)
        hit_layout.addWidget(self.cell_label)
        hit_layout.addLayout(hit_row)
        hit_layout.addWidget(self.hit_view)
        views.addTab(hit_panel, "선택 Row Cell 상세")

        # Record view: KEY-bearing records and their PII cells.
        self.record_view = QTableView()
        self.record_model = PagedTableModel(
            ["행번호", "레코드 키", "KEY 지문", "개인정보 Cell", "검토 Cell",
             " 전체 Cell", "Row 판정"]
        )
        self.record_view.setModel(self.record_model)
        self.record_view.horizontalHeader().setStretchLastSection(True)
        record_panel = QWidget()
        record_layout = QVBoxLayout(record_panel)
        self.record_context = QLabel("KEY 레코드 없음")
        self.prev_record_page = QPushButton("이전 레코드 페이지")
        self.next_record_page = QPushButton("다음 레코드 페이지")
        self.prev_record_page.setEnabled(False)
        self.next_record_page.setEnabled(False)
        record_row = QHBoxLayout()
        record_row.addWidget(self.prev_record_page)
        record_row.addWidget(self.next_record_page)
        record_layout.addWidget(self.record_context)
        record_layout.addLayout(record_row)
        record_layout.addWidget(self.record_view)
        views.addTab(record_panel, "KEY 레코드")

        # Group view: identical PII values collapsed into one entity.
        self.group_view = QTableView()
        self.group_model = PagedTableModel(
            ["개인정보 유형", "마스킹 샘플", "HMAC 지문", "발생", "Row 수", "KEY 레코드 수",
             "컬럼", "첫 Row", "마지막 Row"]
        )
        self.group_view.setModel(self.group_model)
        self.group_view.horizontalHeader().setStretchLastSection(True)
        group_panel = QWidget()
        group_layout = QVBoxLayout(group_panel)
        self.group_context = QLabel("개인정보 그룹 없음")
        self.prev_group_page = QPushButton("이전 그룹 페이지")
        self.next_group_page = QPushButton("다음 그룹 페이지")
        self.prev_group_page.setEnabled(False)
        self.next_group_page.setEnabled(False)
        group_row = QHBoxLayout()
        group_row.addWidget(self.prev_group_page)
        group_row.addWidget(self.next_group_page)
        group_layout.addWidget(self.group_context)
        group_layout.addLayout(group_row)
        group_layout.addWidget(self.group_view)
        views.addTab(group_panel, "개인정보 그룹")

        self._record_page = 0
        self._group_page = 0
        self.prev_record_page.clicked.connect(lambda: self._show_record_page(self._record_page - 1))
        self.next_record_page.clicked.connect(lambda: self._show_record_page(self._record_page + 1))
        self.prev_group_page.clicked.connect(lambda: self._show_group_page(self._group_page - 1))
        self.next_group_page.clicked.connect(lambda: self._show_group_page(self._group_page + 1))

        layout.addWidget(views)
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

        self.status_filter.currentIndexChanged.connect(lambda _: self._show_cell_page(0))
        self.prev_cell_page.clicked.connect(lambda: self._show_cell_page(self._cell_ledger_page - 1))
        self.next_cell_page.clicked.connect(lambda: self._show_cell_page(self._cell_ledger_page + 1))
        self.prev_hit_page.clicked.connect(lambda: self._change_cell_page(-1))
        self.next_hit_page.clicked.connect(lambda: self._change_cell_page(1))
        self._cell_ledger_page = 0

    # ---------------------------------------------------------------- column view
    def show_hits(self, result) -> None:
        rows = result.hit_rows if hasattr(result, "hit_rows") else []
        self.hit_model.set_records([
            {"행번호": hit["row"], "컬럼": result.column, "컬럼 인덱스": getattr(result, "column_index", 0),
             "유형": result.display_name, "Cell 판정": getattr(result, "verdict", "개인정보"),
             "탐지값(마스킹)": hit["value"]}
            for hit in rows
        ])

    def _store_for(self, result):
        return getattr(result, "detail_store", None)

    def row_total(self) -> int:
        total = 0
        for result in self._file_results:
            store = self._store_for(result)
            total += store.row_count() if store is not None else len(getattr(result, "row_records", []))
        return total

    def _page_records(self, file_results: list, offset: int, limit: int):
        remaining = offset
        for result in file_results:
            store = self._store_for(result)
            count = store.row_count() if store is not None else len(getattr(result, "row_records", []))
            if remaining >= count:
                remaining -= count
                continue
            take = min(limit, count - remaining)
            source = store.iter_rows(remaining, take) if store is not None else [
                record.as_dict() for record in result.row_records[remaining:remaining + take]
            ]
            for record in source:
                yield self._display_row(record, store)
            limit -= take
            remaining = 0
            if limit <= 0:
                break

    def _display_row(self, record: dict, store) -> dict:
        """Map a stored row record onto the columns the Row 판정 table shows."""
        return {
            "row": record.get("row", record.get("행번호", 0)),
            "행번호": record.get("row", record.get("행번호", 0)),
            "개인정보 상태": record.get("status") or record.get("개인정보 상태", "-"),
            "확정 개인정보": ", ".join(record.get("confirmed_pii") or [])
                            if isinstance(record.get("confirmed_pii"), list)
                            else (record.get("confirmed_pii") or "-"),
            "검토 필요": ", ".join(record.get("possible_pii") or [])
                         if isinstance(record.get("possible_pii"), list)
                         else (record.get("possible_pii") or "-"),
            "Cell(PII)": record.get("pii_cells", record.get("cell_count", 0)),
            "Cell(검토)": record.get("review_cells", 0),
            "Cell(미평가)": record.get("unrated_cells", 0),
            "Cell(비PII)": record.get("non_pii_cells", 0),
            "Cell(빈)": record.get("empty_cells", 0),
            "Row 위험도": record.get("risk_level", "-"),
            "AI Row 판정": record.get("ai_verdict", "-"),
            "_store": store,
            "_raw": record,
        }

    def show_rows(self, file_results: list, page: int = 0, page_size: int = 200) -> None:
        """Render one page of row verdicts (and its cell detail) from the detail store."""
        self._file_results = file_results
        self._page_size = page_size
        self._page = page
        total = self.row_total()
        records = list(self._page_records(file_results, page * page_size, page_size))
        self.row_model.set_records(records)
        self._row_page = records
        self._selected_row = None
        self._cell_page = 0
        self.hit_model.set_records([])
        self.cell_label.setText("Cell 상세: Row를 선택하면 그 Row의 Cell 상세 (전량 SQLite)")
        self._show_cell_page(0)

        last_page = max(0, (total - 1) // page_size)
        cells_stored = sum(
            self._store_for(result).verdict_count()
            for result in file_results
            if self._store_for(result) is not None
        )
        self.row_context.setText(
            f"Row 판정 {total:,}건 (SQLite) - 페이지 {page + 1}/{last_page + 1}, 이 페이지 {len(records)}건 / "
            f"Cell 판정 {cells_stored:,}건 저장됨"
        )
        self.prev_page.setEnabled(page > 0)
        self.next_page.setEnabled(page < last_page)
        try:
            self._show_record_page(0)
            self._show_group_page(0)
        except Exception:  # a missing aggregation must not hide the column/Row views
            logger.exception("Record/Group view could not be prepared")

    def _change_page(self, delta: int) -> None:
        self.show_rows(self._file_results, self._page + delta, self._page_size)

    def _on_result_row_clicked(self, index: int) -> None:
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
        self._cell_page = max(0, min(self._cell_page, last_page))
        offset = self._cell_page * page_size
        store = record.get("_store")
        if store is not None:
            cells = list(store.cells_for_row(record["row"], offset=offset, limit=page_size))
        else:
            cells = [cell.as_dict() for cell in record.get("_cells", [])][offset:offset + page_size]
        self.hit_model.set_records([
            {"행번호": cell["row"], "컬럼": cell["column"], "컬럼 인덱스": cell["column_index"],
             "유형": cell["display_name"], "Cell 판정": cell["decision"],
             "탐지값(마스킹)": cell["masked_value"]}
            for cell in cells
        ])
        self.cell_label.setText(
            f"Row {record['row']} Cell 상세 {total:,}건 (SQLite) - "
            f"Cell 페이지 {self._cell_page + 1}/{last_page + 1}, 이 페이지 {len(cells)}건 "
            f"(화면 상한 {page_size})"
        )
        self.prev_hit_page.setEnabled(self._cell_page > 0)
        self.next_hit_page.setEnabled(self._cell_page < last_page)

    def _change_cell_page(self, delta: int) -> None:
        self._cell_page += delta
        self._render_cell_page()

    # ---------------------------------------------------------------- cell ledger
    def _show_cell_page(self, page: int) -> None:
        """Page through the complete Cell ledger with an optional status filter."""
        if not self._file_results:
            self.cell_model.set_records([])
            self.cell_context.setText("Cell 판정 목록 없음")
            return
        status = self.status_filter.currentText()
        filter_status = None if status == "전체" else status
        page = max(0, page)
        total = 0
        for result in self._file_results:
            store = self._store_for(result)
            if store is not None:
                total += store.verdict_count(filter_status)
        remaining = page * self.cell_page_size
        records = []
        for result in self._file_results:
            store = self._store_for(result)
            if store is None:
                continue
            count = store.verdict_count(filter_status)
            if remaining >= count:
                remaining -= count
                continue
            take = min(self.cell_page_size - len(records), count - remaining)
            if take <= 0:
                break
            for cell in store.iter_cell_verdicts(filter_status, offset=remaining, limit=take):
                records.append({
                    "행번호": cell["row"], "컬럼": cell["column"], "컬럼 인덱스": cell["column_index"],
                    "Cell 판정": cell["cell_status"], "개인정보 유형": cell["privacy_types"],
                    "규칙 판정": cell["rule_verdict"], "AI 판정": cell["ai_verdict"] or "-",
                    "최종 판정": cell["final_verdict"] or cell["cell_status"],
                    "신뢰도": round(cell["confidence"], 3), "판단 방법": cell["decision_method"],
                    "검토": "예" if cell["needs_review"] else "아니요",
                    "마스킹 값": cell["masked_value"],
                })
            remaining = 0
        self.cell_model.set_records(records)
        last_page = max(0, (total - 1) // self.cell_page_size)
        self._cell_ledger_page = min(page, last_page)
        breakdown = {}
        for result in self._file_results:
            store = self._store_for(result)
            if store is not None:
                for key, value in store.status_breakdown().items():
                    breakdown[key] = breakdown.get(key, 0) + value
        self.cell_context.setText(
            f"Cell 판정 {total:,}건 (필터: {status}) - 페이지 {self._cell_ledger_page + 1}/{last_page + 1}, "
            f"이 페이지 {len(records)}건 | " + ", ".join(f"{key} {value:,}" for key, value in breakdown.items())
        )
        self.prev_cell_page.setEnabled(self._cell_ledger_page > 0)
        self.next_cell_page.setEnabled(self._cell_ledger_page < last_page)

    # ---------------------------------------------------------------- column view
    def status_breakdown(self) -> dict[str, int]:
        """Cell status totals across every analysed file in this result set."""
        totals: dict[str, int] = {}
        for result in self._file_results:
            store = self._store_for(result)
            if store is None:
                continue
            for status, count in store.status_breakdown().items():
                totals[status] = totals.get(status, 0) + count
        return totals

    def _show_record_page(self, page: int) -> None:
        page = max(0, page)
        store = self._store_for(self._file_results[-1]) if self._file_results else None
        if store is None:
            return
        total = store.record_count()
        if page * self.cell_page_size >= total:
            return
        self._record_page = page
        rows = []
        for record in store.iter_records(limit=self.cell_page_size, offset=page * self.cell_page_size):
            rows.append({
                "행번호": record["row"],
                "레코드 키": record["row_key"],
                "KEY 지문": record["key_fingerprint"] or "(KEY 없음)",
                "개인정보 Cell": record["pii_cells"],
                "검토 Cell": record["review_cells"],
                " 전체 Cell": record["total_cells"],
                "Row 판정": record["status"],
            })
        self.record_model.set_records(rows)
        self.record_context.setText(f"KEY 레코드 {page * self.cell_page_size + 1}-"
                                    f"{min(total, (page + 1) * self.cell_page_size)} / {total:,}건")
        self.prev_record_page.setEnabled(page > 0)
        self.next_record_page.setEnabled((page + 1) * self.cell_page_size < total)

    def _show_group_page(self, page: int) -> None:
        page = max(0, page)
        store = self._store_for(self._file_results[-1]) if self._file_results else None
        if store is None:
            return
        store.build_groups()
        total = store.group_count()
        if page * self.cell_page_size >= total:
            return
        self._group_page = page
        rows = []
        for group in store.iter_groups(limit=self.cell_page_size, offset=page * self.cell_page_size):
            rows.append({
                "개인정보 유형": group["pii_type"] or "-",
                "마스킹 샘플": group.get("masked_sample") or "-",
                "HMAC 지문": group["fingerprint"],
                "발생": group["occurrences"],
                "Row 수": group["distinct_rows"],
                "KEY 레코드 수": group["distinct_records"],
                "컬럼": ", ".join(group["columns"]),
                "첫 Row": group["first_row"],
                "마지막 Row": group["last_row"],
            })
        self.group_model.set_records(rows)
        self.group_context.setText(f"개인정보 그룹 {page * self.cell_page_size + 1}-"
                                   f"{min(total, (page + 1) * self.cell_page_size)} / {total:,}건")
        self.prev_group_page.setEnabled(page > 0)
        self.next_group_page.setEnabled((page + 1) * self.cell_page_size < total)

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
