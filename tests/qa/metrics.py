"""Reusable accuracy metrics for the independent QA harness.

Ground truth lives in tests/qa/dataset.py. Nothing here knows about detector
implementation, so the numbers are independent of the code being tested.

Two measurement levels are reported:

* detector level  - every detector is invoked directly on every labelled value.
* pipeline level  - analyse_file() on a generated CSV, i.e. what the engine
  (tag gating, noise filter, column-name affinity) actually reports.
"""
from __future__ import annotations

import csv
import tempfile
from collections import defaultdict
from pathlib import Path

from app.config.settings import AnalysisConfig
from app.core.analysis_service import analyse_file
from app.core.privacy_engine import build_detectors
from app.detectors.base import profile_of
from tests.qa.dataset import CODE_GROUP_COLUMNS, DATASET, PROTECTION_COLUMNS

# Natural column name for each privacy type (column-level tests).
COLUMN_NAMES = {
    "RESIDENT_ID": "RESIDENT_NO",
    "PASSPORT": "PASSPORT_NO",
    "DRIVER_LICENSE": "DRIVER_LICENSE_NO",
    "FOREIGNER_ID": "FOREIGNER_ID",
    "SOCIAL_SECURITY": "SSN",
    "INSURANCE_NUMBER": "NHI_NO",
    "CARD_NUMBER": "CARD_NO",
    "BANK_ACCOUNT": "BANK_ACCOUNT",
    "EMAIL": "EMAIL",
    "PHONE_NUMBER": "PHONE",
    "NAME": "CUSTOMER_NAME",
    "ADDRESS": "DETAIL_ADDRESS",
    "BIRTH_DATE": "BIRTH_DATE",
    "LOCATION": "COORD",
    "IMSI": "IMSI",
    "IMEI": "IMEI",
    "UUID": "UUID",
    "MAC": "MAC_ADDR",
    "IP": "CLIENT_IP",
    "SN": "SERIAL_NO",
    "POSTCODE": "POSTCODE",
    "CUSTOM_SEC_ID": "SEC_ID",
    "CUSTOM_ID_SUFFIX": "ID_SUFFIX",
    "CUSTOM_WILDCARD_ID": "WILDCARD_ID",
}


def _metrics(tp: dict, fp: dict, fn: dict, positives: dict) -> dict:
    out = {}
    total = len(DATASET)
    for type_key in sorted(set(tp) | set(fp) | set(fn)):
        precision = tp[type_key] / (tp[type_key] + fp[type_key]) if tp[type_key] + fp[type_key] else 0.0
        recall = tp[type_key] / (tp[type_key] + fn[type_key]) if tp[type_key] + fn[type_key] else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        out[type_key] = {
            "positives": positives[type_key],
            "tp": tp[type_key],
            "fp": fp[type_key],
            "fn": fn[type_key],
            "tn": total - positives[type_key] - fp[type_key],
            "precision": round(precision, 3),
            "recall": round(recall, 3),
            "f1": round(f1, 3),
        }
    return out


def detector_metrics(column_hint: bool = False) -> dict:
    """Value-level precision/recall/F1 per privacy type.

    column_hint=True passes the column name a real CSV would have. Ambiguous
    bare numbers (12-digit foreigner/insurance/account, 15-digit IMSI/IMEI) are
    only reported when the column name supplies affinity evidence - that is the
    documented design, and measuring both ways makes the trade-off visible.
    """
    detectors = build_detectors(AnalysisConfig.load())
    positives: dict[str, int] = defaultdict(int)
    tp: dict[str, int] = defaultdict(int)
    fn: dict[str, int] = defaultdict(int)
    fp: dict[str, int] = defaultdict(int)
    fp_examples: dict[str, list] = defaultdict(list)
    fn_examples: dict[str, list] = defaultdict(list)

    for value, primary, also_ok, note in DATASET:
        profile = profile_of(value)
        column = COLUMN_NAMES.get(primary, "VALUE") if (column_hint and primary) else ""
        fired = set()
        for detector in detectors:
            match = detector.match(value, profile, column)
            if match:
                fired.add(match.type_key)
        if primary:
            positives[primary] += 1
            if primary in fired:
                tp[primary] += 1
            else:
                fn[primary] += 1
                if len(fn_examples[primary]) < 6:
                    fn_examples[primary].append({"value": value, "note": note, "fired": sorted(fired)})
        for type_key in fired:
            if type_key == primary or type_key in also_ok:
                continue
            fp[type_key] += 1
            if len(fp_examples[type_key]) < 6:
                fp_examples[type_key].append({"value": value, "intended": primary, "note": note})

    out = _metrics(tp, fp, fn, positives)
    for type_key, m in out.items():
        m["fp_examples"] = fp_examples[type_key]
        m["fn_examples"] = fn_examples[type_key]
    return out


def build_column_csv(path: Path) -> Path:
    """Write one column per privacy type plus an adversarial noise column."""
    types = sorted({primary for _, primary, _, _ in DATASET if primary})
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        header = [COLUMN_NAMES.get(t, f"COL_{t}") for t in types] + ["MIXED_NOISE"]
        columns = [[v for v, primary, _, _ in DATASET if primary == t] for t in types]
        columns.append([v for v, primary, _, _ in DATASET if primary is None])
        writer.writerow(header)
        width = max(len(c) for c in columns)
        for i in range(width):
            writer.writerow([c[i] if i < len(c) else "" for c in columns])
    return path


def pipeline_metrics(csv_path: Path | None = None) -> dict:
    """Column-level result as the engine reports it, plus per-type counts."""
    types = sorted({primary for _, primary, _, _ in DATASET if primary})
    cleanup = False
    if csv_path is None:
        tmp = tempfile.mkdtemp()
        csv_path = Path(tmp) / "qa_columns.csv"
        build_column_csv(csv_path)
        cleanup = True

    findings = analyse_file(csv_path, AnalysisConfig.load()).findings
    reported = {finding["column"]: {t["type_key"] for t in finding["types"]} for finding in findings}

    out = {}
    noise_column = "MIXED_NOISE"
    for type_key in types:
        column = COLUMN_NAMES.get(type_key, f"COL_{type_key}")
        hits = reported.get(column, set())
        hits_elsewhere = [f["column"] for f in findings
                          if f["column"] != column and type_key in {t["type_key"] for t in f["types"]}]
        positives = sum(1 for _, primary, _, _ in DATASET if primary == type_key)
        out[type_key] = {
            "column": column,
            "positives": positives,
            "detected": type_key in hits,
            "also_reported": sorted(hits - {type_key}),
            "reported_on_other_columns": sorted(hits_elsewhere),
            "noise_column_hit": type_key in reported.get(noise_column, set()),
        }
    if cleanup:
        csv_path.unlink()
        Path(csv_path).parent.rmdir()
    return out


def protection_metrics() -> dict:
    columns = list(PROTECTION_COLUMNS) + list(CODE_GROUP_COLUMNS)
    width = max(len(values) for _, values in columns)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "qa_protection.csv"
        with open(path, "w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow([name for name, _ in columns])
            for i in range(width):
                writer.writerow([values[i] if i < len(values) else "" for _, values in columns])
        result = analyse_file(path)
    return {
        "protection": [g.as_dict() for g in result.protection],
        "code_groups": [g.as_dict() for g in result.code_groups],
        "rows_parsed": result.rows_parsed,
    }


def _ratios(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def cell_row_metrics(natural_names: bool = True) -> dict:
    """Cell-level and row-level precision/recall/F1 over the labelled dataset.

    One row per labelled value; the value sits in the column matching its type
    (natural column names, or meaningless FIELD_01 style names) and a benign
    cell travels with it. Ground truth is per cell, so both the cell verdict and
    the row verdict can be scored independently of the column classification.
    """
    from app.export.hit_exporter import collect_hit_cells, collect_row_records

    types = sorted({primary for _, primary, _, _ in DATASET if primary})
    noise = ["normal text", "42", "reference-1", "2024-01-05T10:00:00", "1.5"]
    if natural_names:
        header = [COLUMN_NAMES.get(t, f"COL_{t}") for t in types] + ["MIXED_NOISE", "NOTE"]
    else:
        header = [f"FIELD_{i + 1:02d}" for i in range(len(types) + 2)]
    type_index = {t: i for i, t in enumerate(types)}
    noise_column = len(types)

    rows: list[list[str]] = []
    truth: dict[tuple[int, int], tuple[str | None, tuple[str, ...]]] = {}
    for row_number, (value, primary, also_ok, _note) in enumerate(DATASET, start=1):
        cells = [""] * len(header)
        column = type_index[primary] if primary is not None else noise_column
        cells[column] = value
        cells[-1] = noise[row_number % len(noise)]
        rows.append(cells)
        truth[(row_number, column)] = (primary, also_ok)
        for index in range(len(header)):
            if index != column:
                truth[(row_number, index)] = (None, ())

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "qa_rows.csv"
        with open(path, "w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            writer.writerows(rows)
        result = analyse_file(path, AnalysisConfig(use_ai=False))

    hits = collect_hit_cells([result])
    cell_tp = cell_fp = cell_fn = 0
    per_type: dict[str, dict[str, int]] = {}
    for cell in hits:
        expected, also_ok = truth.get((cell["row_number"], cell["column_index"]), (None, ()))
        entry = per_type.setdefault(cell["privacy_type"], {"tp": 0, "fp": 0, "fn": 0})
        if expected is not None and (cell["privacy_type"] == expected or cell["privacy_type"] in also_ok):
            cell_tp += 1
            entry["tp"] += 1
        else:
            cell_fp += 1
            entry["fp"] += 1
    for (row, index), (expected, _also_ok) in truth.items():
        if expected is None:
            continue
        if not any(c["row_number"] == row and c["column_index"] == index
                   and c["privacy_type"] == expected for c in hits):
            cell_fn += 1
            per_type.setdefault(expected, {"tp": 0, "fp": 0, "fn": 0})["fn"] += 1

    flagged = {r["row_number"] for r in collect_row_records([result]) if r["has_pii"] == "예"}
    expected_rows = {row for (row, _index), (primary, _o) in truth.items() if primary is not None}
    row_tp = len(flagged & expected_rows)
    row_fp = len(flagged - expected_rows)
    row_fn = len(expected_rows - flagged)
    cell_precision, cell_recall, cell_f1 = _ratios(cell_tp, cell_fp, cell_fn)
    row_precision, row_recall, row_f1 = _ratios(row_tp, row_fp, row_fn)
    return {
        "cell": {"tp": cell_tp, "fp": cell_fp, "fn": cell_fn,
                 "precision": round(cell_precision, 3), "recall": round(cell_recall, 3),
                 "f1": round(cell_f1, 3)},
        "row": {"tp": row_tp, "fp": row_fp, "fn": row_fn,
                "precision": round(row_precision, 3), "recall": round(row_recall, 3),
                "f1": round(row_f1, 3)},
        "per_type": per_type,
        "counts": result.cell_counts,
    }
