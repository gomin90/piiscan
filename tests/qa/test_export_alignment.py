"""Export column alignment: the header row and every data row must agree."""
from __future__ import annotations

import csv
from pathlib import Path

from app.config.settings import AnalysisConfig
from app.core.analysis_service import analyse_file
from app.export.hit_exporter import CELL_HEADERS, ROW_HEADERS, write_hit_rows, write_row_records


def test_export_columns_line_up_with_their_headers(tmp_path: Path) -> None:
    path = tmp_path / "align.csv"
    path.write_text("IDX,NAME,PHONE\n1,박민,010-1234-5678\n2,900101-1234567,abc\n", encoding="utf-8")
    result = analyse_file(str(path), AnalysisConfig(use_ai=False))

    hits = write_hit_rows([result], tmp_path / "hits.csv")
    rows = write_row_records([result], tmp_path / "rows.csv")

    for file, headers in ((hits, CELL_HEADERS), (rows, ROW_HEADERS)):
        data = list(csv.reader(open(file, encoding="utf-8-sig")))
        assert data[0] == headers, f"{file} header mismatch"
        assert all(len(record) == len(headers) for record in data[1:]), f"{file} has ragged rows"

    row = list(csv.DictReader(open(rows, encoding="utf-8-sig")))[0]
    assert row["행번호"] == "1"
    assert "PHONE_NUMBER" in row["확정 개인정보"]
    assert row["검토 필요 개인정보"] == ""
    assert "PHONE" in row["발생 컬럼"]

    cells = list(csv.DictReader(open(hits, encoding="utf-8-sig")))
    phone_cells = [cell for cell in cells if cell["컬럼명"] == "PHONE"]
    assert phone_cells, "the phone column produced no cell records"
    assert phone_cells[0]["행번호"] == "1"
    assert phone_cells[0]["Row 판정"]
    assert write_hit_rows.rows_written == len(list(csv.reader(open(hits, encoding="utf-8-sig")))) - 1