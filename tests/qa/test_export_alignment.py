"""Export column alignment: the header row and every data row must agree."""
from __future__ import annotations

import csv
from pathlib import Path

from app.config.settings import AnalysisConfig
from app.core.analysis_service import analyse_file
from app.export.hit_exporter import (CELL_HEADERS, ROW_HEADERS, iter_cell_records,
                                     write_hit_rows, write_row_records)


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


def test_multi_type_cell_lists_type_names(tmp_path: Path) -> None:
    """A cell carrying several PII types must export the type names, not its stored string."""
    path = tmp_path / "multi.csv"
    path.write_text("IDX,NOTE\n1,홍길동 010-1234-5678 user@example.com\n", encoding="utf-8")
    result = analyse_file(str(path), AnalysisConfig(use_ai=False))

    cells = list(iter_cell_records([result]))
    note = next(cell for cell in cells if cell["column"] == "NOTE")
    parts = [part for part in note["privacy_types"].split(", ") if part]
    assert len(parts) >= 2, f"expected a multi-type cell, got {note['privacy_types']!r}"
    assert all(len(part) > 1 for part in parts), f"types split into characters: {parts}"


def test_multi_type_cell_is_rendered_as_type_names_not_characters(tmp_path: Path) -> None:
    """A cell carrying several PII types must export the type names, not its stored string char-by-char."""
    path = tmp_path / "multi.csv"
    path.write_text("IDX,NOTE\n1,홍길동 010-1234-5678 user@example.com\n", encoding="utf-8")
    result = analyse_file(str(path), AnalysisConfig(use_ai=False))

    cells = list(iter_cell_records([result]))
    note = next(cell for cell in cells if cell["column"] == "NOTE")
    types = note["privacy_types"]
    parts = [part for part in types.split(", ") if part]
    assert len(parts) >= 2, f"expected a multi-type cell, got {types!r}"
    assert all(len(part) > 1 for part in parts), f"types were split into characters: {types!r}"