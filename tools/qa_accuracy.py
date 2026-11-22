#!/usr/bin/env python3
"""Independent QA harness: per-privacy-type accuracy (precision/recall/F1/FP/FN).

Two levels are measured:
  1) detector level  - every detector is run directly against every labelled
     value, so detector-specific false positives are visible.
  2) pipeline level  - analyse_file() on a generated CSV, i.e. what the
     engine (tag gating, noise filter, name affinity) actually reports.

Usage:
    python tools/qa_accuracy.py [--json results/qa_accuracy.json]
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config.settings import AnalysisConfig
from app.core.analysis_service import analyse_file
from app.core.privacy_engine import build_detectors
from app.detectors.base import profile_of
from tests.qa.dataset import DATASET, PROTECTION_COLUMNS, CODE_GROUP_COLUMNS
from tests.qa.metrics import cell_row_metrics

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


def detector_metrics(column_hint: bool = False) -> dict:
    """Value-level accuracy.

    column_hint=True gives each value the column name a real CSV would have
    (e.g. "BANK_ACCOUNT"). That is how the tool is actually used, and it also
    shows the intentional design: ambiguous bare numbers are only reported when
    the column name provides affinity evidence.
    """
    detectors = build_detectors(AnalysisConfig.load())
    positives = defaultdict(int)
    tp = defaultdict(int)
    fn = defaultdict(int)
    fp = defaultdict(int)
    fp_examples = defaultdict(list)
    fn_examples = defaultdict(list)

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

    types = set(tp) | set(fn) | set(fp)
    out = {}
    total = len(DATASET)
    for type_key in types:
        precision = tp[type_key] / (tp[type_key] + fp[type_key]) if (tp[type_key] + fp[type_key]) else 0.0
        recall = tp[type_key] / (tp[type_key] + fn[type_key]) if (tp[type_key] + fn[type_key]) else 0.0
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
            "fp_examples": fp_examples[type_key],
            "fn_examples": fn_examples[type_key],
        }
    return out


def pipeline_metrics() -> dict:
    """Per-type column-level result as the engine actually reports it."""
    detectors = build_detectors(AnalysisConfig.load())
    types = sorted({primary for _, primary, _, _ in DATASET if primary})
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "qa_columns.csv"
        with open(path, "w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            header, columns = [], []
            for type_key in types:
                header.append(COLUMN_NAMES.get(type_key, f"COL_{type_key}"))
                columns.append([v for v, primary, _, _ in DATASET if primary == type_key])
            # adversarial column: every value that is NOT of this type
            header.append("MIXED_NOISE")
            columns.append([v for v, primary, _, _ in DATASET if primary is None])
            writer.writerow(header)
            width = max(len(c) for c in columns)
            for i in range(width):
                writer.writerow([c[i] if i < len(c) else "" for c in columns])

        findings = analyse_file(path, AnalysisConfig.load()).findings

    reported = {}
    for finding in findings:
        reported[finding["column"]] = {t["type_key"] for t in finding["types"]}

    out = {}
    noise_reported = reported.get("MIXED_NOISE", set())
    for type_key in types:
        column = COLUMN_NAMES.get(type_key, f"COL_{type_key}")
        hits = reported.get(column, set())
        positives = sum(1 for _, primary, _, _ in DATASET if primary == type_key)
        out[type_key] = {
            "column": column,
            "positives": positives,
            "detected": type_key in hits,
            "also_reported": sorted(hits - {type_key}),
            "reported_on_other_columns": sorted(
                f["column"] for f in findings
                if f["column"] != column and type_key in {t["type_key"] for t in f["types"]}
            ),
            "noise_column_type": type_key in noise_reported,
        }
    return out


def protection_metrics() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "qa_protection.csv"
        columns = list(PROTECTION_COLUMNS) + list(CODE_GROUP_COLUMNS)
        width = max(len(values) for _, values in columns)
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Privacy Scanner QA accuracy harness")
    parser.add_argument("--json", default=str(ROOT / "results" / "qa_accuracy.json"))
    args = parser.parse_args()

    det = detector_metrics(column_hint=False)
    det_hinted = detector_metrics(column_hint=True)
    pipe = pipeline_metrics()
    prot = protection_metrics()
    cell_ctx = cell_row_metrics(natural_names=True)
    cell_plain = cell_row_metrics(natural_names=False)

    print("=== detector-level accuracy (value level) ===")
    print(f"{'type':20} {'pos':>4} {'TP':>3} {'FP':>3} {'FN':>3} {'P':>6} {'R':>6} {'F1':>6}")
    for type_key, m in sorted(det.items(), key=lambda kv: (-kv[1]["positives"], kv[0])):
        if m["positives"] == 0 and m["fp"] == 0:
            continue
        print(f"{type_key:20} {m['positives']:>4} {m['tp']:>3} {m['fp']:>3} {m['fn']:>3} "
              f"{m['precision']:>6} {m['recall']:>6} {m['f1']:>6}")
    for type_key, m in det.items():
        if m["fp"]:
            print(f"  FP {type_key}: {m['fp_examples']}")

    print("\n=== pipeline-level (per column) ===")
    for type_key, m in pipe.items():
        print(f"{type_key:20} column={m['column']:18} detected={m['detected']} "
              f"also={m['also_reported']} noise_column={m['noise_column_type']}")

    print("\n=== protection / code group ===")
    print(json.dumps(prot, ensure_ascii=False, indent=2))

    print("\n=== cell-level / row-level (Row 전수 scan) ===")
    for label, metric in (("column-name context", cell_ctx), ("meaningless column names", cell_plain)):
        c, r = metric["cell"], metric["row"]
        print(f"{label:26} cell P/R/F1 = {c['precision']:.3f}/{c['recall']:.3f}/{c['f1']:.3f} "
              f"(TP {c['tp']}, FP {c['fp']}, FN {c['fn']})  |  "
              f"row P/R/F1 = {r['precision']:.3f}/{r['recall']:.3f}/{r['f1']:.3f} "
              f"(TP {r['tp']}, FP {r['fp']}, FN {r['fn']})")

    Path(args.json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json).write_text(json.dumps(
        {"detector": det, "detector_with_column_hint": det_hinted,
         "pipeline": pipe, "protection": prot,
         "cell_row_context": cell_ctx, "cell_row_no_context": cell_plain},
        ensure_ascii=False, indent=2
    ), encoding="utf-8")
    print(f"\nreport written to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
