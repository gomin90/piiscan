"""Checkpoint / resume semantics of `--resume`.

A cancelled run must be continuable: every row already decided is kept, the rows
that were never reached are analysed on the next run, and no row is written
twice.  The assertions compare the resumed ledger against a single full pass.
"""
from __future__ import annotations

import csv
from pathlib import Path

from app.config.settings import AnalysisConfig
from app.core.analysis_service import analyse_file
from app.utils.logger import Diagnostics


def _fixture(path: Path, rows: int = 120, columns: int = 5) -> Path:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["SEQ_NO", "NAME", "PHONE", "NOTE", "AMOUNT"])
        for row in range(rows):
            writer.writerow([
                f"SEQ-{row}",
                "홍길동" if row % 3 == 0 else f"customer-{row}",
                f"010-1234-{row:04d}",
                "" if row % 7 == 0 else f"order {row}",
                f"{row * 1000}",
            ])
    return path


def test_resume_continues_without_duplicating_rows(tmp_path):
    fixture = _fixture(tmp_path / "data.csv")
    store_path = tmp_path / "detail.sqlite"

    inspected = {"rows": 0}

    # `should_stop` is consulted once per source row, so this cancels after
    # exactly 40 rows of the 120-row file.
    def stop_after_40() -> bool:
        inspected["rows"] += 1
        return inspected["rows"] > 40

    first_diag = Diagnostics(enabled=True)
    partial = analyse_file(fixture, AnalysisConfig(use_ai=False, resume=True,
                                                   detail_store_path=str(store_path)),
                           should_stop=stop_after_40,
                           diagnostics=first_diag)
    first_store = partial.detail_store
    first_logical = first_store.logical_cell_count()
    assert partial.cancelled, partial.cell_counts
    assert 0 < first_logical < 120 * 5, first_logical
    first_store.close()

    second_diag = Diagnostics(enabled=True)
    resumed = analyse_file(fixture, AnalysisConfig(use_ai=False, resume=True,
                                                   detail_store_path=str(store_path)),
                           diagnostics=second_diag)
    store = resumed.detail_store
    diagnostics = {entry["event"]: entry for entry in second_diag.events}
    assert "resume_checkpoint" in diagnostics, diagnostics.keys()

    logical = store.logical_cell_count()
    completeness = store.verify_completeness()
    assert logical == 120 * 5, logical
    assert completeness["complete"], completeness

    rows = store._exec(
        "SELECT COUNT(DISTINCT row) FROM row_status WHERE analysis_id = ?",
        (store.analysis_id,),
    ).fetchone()[0]
    assert rows == 120, rows
    # The checkpoint recorded by the cancelled run is exactly where it stopped.
    assert diagnostics["resume_checkpoint"]["resumed_from_row"] * 5 == first_logical

    # A resumed ledger must equal a single full pass, cell for cell.
    full = analyse_file(fixture, AnalysisConfig(use_ai=False,
                                                detail_store_path=str(tmp_path / "full.sqlite")))
    full_status = full.detail_store.status_breakdown()
    assert completeness["status_counts"] == full_status, (completeness, full_status)

    resumed.detail_store.cleanup()
    full.detail_store.cleanup()


