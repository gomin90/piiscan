"""Measure how the GUI result views behave against a real 3,000,000-cell store.

The GUI never materialises a result set: `PagedTableModel` asks the detail store
for one page at a time. This probe runs the production queries the models use
(row verdicts, the complete Cell ledger, per-Row Cell detail, KEY records and
PII groups) against a full 150,000 x 20 store, times deep page fetches, and
records how many rows each page actually returns. Peak RSS is sampled while the
pages are walked so "no bulk load" is a measurement, not a claim.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from app.config.settings import AnalysisConfig
from app.core.analysis_service import analyse_file
from app.core.cell_verdict import STATUS_KEY, STATUS_PII
from app.core.detail_store import DetailStore
from app.ui.table_model import PagedTableModel

ROWS = 150_000
COLUMNS = 20
KEYS = ["SEQ_NO", "RECORD_KEY"]
PAGE = 200


def build_fixture(path: Path) -> None:
    if path.exists():
        return
    import random
    rng = random.Random(11)
    columns = ["SEQ_NO", "RECORD_KEY", "CUSTOMER_NAME", "PHONE_NO"] + [
        f"COL_{index}" for index in range(COLUMNS - 4)
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(",".join(columns) + "\n")
        for row in range(1, ROWS + 1):
            cells = [str(row), f"REC-{row}"]
            for column in columns[2:]:
                if column == "CUSTOMER_NAME":
                    cells.append(rng.choice(["김민준", "홍길동", "이서연"]))
                elif column == "PHONE_NO":
                    cells.append(f"010-{rng.randint(1000, 9999)}-{rng.randint(1000, 9999)}")
                else:
                    cells.append(rng.choice(["", "ORD-2024-001", "1234567890123456",
                                            "강남구 Sequoia-ro 121", "담당자: 박도윤"]))
            handle.write(",".join(cells) + "\n")


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("results/gui_paging_measured.json")
    out.parent.mkdir(exist_ok=True)
    fixture = out.parent / "scale" / f"bench_{ROWS}x{COLUMNS}.csv"
    build_fixture(fixture)
    store_path = out.parent / "gui_probe.sqlite"
    if store_path.exists():
        store_path.unlink()

    peak = 0
    stop = threading.Event()
    import psutil

    def sample():
        nonlocal peak
        process = psutil.Process()
        while not stop.is_set():
            peak = max(peak, process.memory_info().rss)
            time.sleep(0.05)

    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()

    config = AnalysisConfig(batch_size=2000, key_columns=KEYS, use_ai=False,
                            detail_store_path=str(store_path))
    t0 = time.time()
    result = analyse_file(str(fixture), config)
    analysis_seconds = time.time() - t0
    store = result.detail_store

    pages: dict[str, list[dict]] = {}
    model = PagedTableModel(["행번호", "개인정보 상태"])

    def page_fetch(name: str, page: int, fetch):
        start = time.time()
        records = list(fetch(offset=page * PAGE, limit=PAGE))
        model.set_records(records)
        pages.setdefault(name, []).append({
            "page": page,
            "rows": model.rowCount(),
            "seconds": round(time.time() - start, 3),
        })

    row_total = store.row_count()
    cell_total = store.logical_cell_count()
    pii_total = store.status_breakdown().get(STATUS_PII, 0)

    # Row verdict pages: first, middle, last.
    for page in (0, row_total // PAGE // 2, (row_total - 1) // PAGE):
        page_fetch("row_verdicts", page, store.iter_row_verdicts)

    # Complete Cell ledger, filtered and unfiltered, at deep offsets.
    for page in (0, 100, 1000, (cell_total - 1) // PAGE):
        page_fetch("cell_ledger_all", page, store.iter_cell_verdicts)
    for page in (0, 100, 1000):
        page_fetch("cell_ledger_pii", page, lambda offset, limit: store.iter_cell_verdicts(
            STATUS_PII, offset=offset, limit=limit))
    for page in (0, 100):
        page_fetch("cell_ledger_key", page, lambda offset, limit: store.iter_cell_verdicts(
            STATUS_KEY, offset=offset, limit=limit))

    # Per-Row Cell detail for the widest Row, and the KEY record / group views.
    start = time.time()
    row_cells = list(store.cells_for_row(75_000, offset=0, limit=PAGE))
    detail_seconds = round(time.time() - start, 3)
    start = time.time()
    records = list(store.iter_records(offset=row_total - PAGE, limit=PAGE))
    record_seconds = round(time.time() - start, 3)
    start = time.time()
    groups = list(store.iter_groups(limit=PAGE, offset=500 * PAGE))
    group_seconds = round(time.time() - start, 3)

    stop.set()
    store.close()
    store.cleanup()
    peak_mb = peak / 1024 / 1024

    payload = {
        "fixture": f"{ROWS} rows x {COLUMNS} columns, KEY {KEYS}, rule-only",
        "page_size": PAGE,
        "analysis_seconds": round(analysis_seconds, 2),
        "row_page_count": (row_total + PAGE - 1) // PAGE,
        "cell_page_count": (cell_total + PAGE - 1) // PAGE,
        "total_rows": row_total,
        "total_cells": cell_total,
        "pii_cells": pii_total,
        "pages": pages,
        "row_cell_detail_rows": len(row_cells),
        "row_cell_detail_seconds": detail_seconds,
        "key_record_page_rows": len(records),
        "key_record_page_seconds": record_seconds,
        "group_page_rows": len(groups),
        "group_page_seconds": group_seconds,
        "peak_rss_mb_during_paging": round(peak_mb, 1),
        "note": "rows per page never exceed the page size; no query materialises the whole ledger",
    }
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
