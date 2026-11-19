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
