"""Cell-level gold-set accuracy harness (rule-only vs rule+AI).

Runs the real pipeline over tests/fixtures/gold_cells.py - structured values,
natural-language cells and deliberate negative controls - and reports
Precision / Recall / F1 / FP / FN per privacy type from the Cell ledger itself.

    python tools/gold_accuracy.py --json results/gold_accuracy.json

Counting rules (documented so the numbers can be reproduced):
  * a Cell is a True Positive for type T when the Cell is final-personal and its
    type set contains T, and the gold label for that Cell contains T;
  * False Positive: final-personal Cell whose gold label does not contain T
    (either the Cell is not personal at all, or it is personal of another type);
  * False Negative: gold-personal Cell of type T that the pipeline never marked
    personal (검토 필요 counts as "not confirmed", so it is a miss);
  * Cells the pipeline left as REVIEW (검토 필요) are reported separately as
    "unresolved", never as a hit and never as a clean negative.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import AnalysisConfig
from app.core.analysis_service import analyse_file
from app.core.cell_verdict import STATUS_PII, STATUS_REVIEW
from app.core.privacy_engine import build_detectors
from tests.fixtures.gold_cells import build_csv, gold_labels


def type_key_map() -> dict[str, str]:
    """Detector display names -> canonical type keys, so gold labels and verdicts
    compare on the same vocabulary."""
    mapping = {}
    for detector in build_detectors():
        mapping[detector.display_name] = detector.type_key
        mapping[detector.type_key] = detector.type_key
    # The location detector reports per-axis sub-types that are not separate
    # detector objects, so their display names need explicit aliases.
    mapping.update({"위도": "LATITUDE", "경도": "LONGITUDE",
                   "위치좌표(위도 범위)": "LOCATION", "위치좌표(경도 범위)": "LOCATION"})
    return mapping


def run_gold(csv_path: Path, use_ai: bool, key_columns: list[str]) -> dict:
    names = type_key_map()
    config = AnalysisConfig(
        use_ai=use_ai,
        ai_cell_reviews="none" if not use_ai else "selective",
        ai_column_reviews="none" if not use_ai else "selective",
        key_columns=key_columns,
        table="GOLD",
        schema="QA",
        db_type="QA",
    )
    started = time.perf_counter()
    result = analyse_file(csv_path, config)
    elapsed = time.perf_counter() - started
    store = result.detail_store

    labels = gold_labels()
    stats: dict[str, dict[str, int]] = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0, "tn": 0})
    unresolved: dict[str, int] = defaultdict(int)
    misses: list[dict] = []
    status_totals: dict[str, int] = defaultdict(int)
    cell_count = 0
    for cell in store.iter_cell_verdicts():
        cell_count += 1
        status = cell["cell_status"]
        status_totals[status] += 1
        key = (cell["row"], cell["column"])
        gold_status, gold_types = labels.get(key, ("NOT_PERSONAL", ()))
        predicted_types = {names.get(t, t) for t in (cell["privacy_types"] or "").split(",") if t}
        if status == STATUS_PII:
            if gold_status == "PERSONAL":
                for kind in predicted_types & set(gold_types):
                    stats[kind]["tp"] += 1
                for kind in predicted_types - set(gold_types):
                    stats[kind]["fp"] += 1
                if not predicted_types:
                    stats["(유형 없음)"]["fp"] += 1
            else:
                for kind in predicted_types or {"(유형 없음)"}:
                    stats[kind]["fp"] += 1
        elif status == STATUS_REVIEW:
            for kind in gold_types or predicted_types:
                unresolved[kind] += 1
            # "검토 필요" is not a confirmed hit, so for recall it is still a miss.
            for kind in gold_types:
                stats[kind]["fn"] += 1
        else:  # non-personal, empty, key-excluded, unrated
            for kind in gold_types:
                stats[kind]["fn"] += 1
            stats["(비개인정보)"]["tn"] += 1
        if status == STATUS_PII and gold_status != "PERSONAL":
            misses.append({"kind": "false_positive", "row": cell["row"], "column": cell["column"],
                           "value": cell["masked_value"], "predicted": sorted(predicted_types),
                           "gold": gold_status})
        elif gold_status == "PERSONAL" and status != STATUS_PII:
            misses.append({"kind": "unresolved" if status == STATUS_REVIEW else "false_negative",
                           "row": cell["row"], "column": cell["column"],
                           "value": cell["masked_value"], "status": status,
                           "gold_types": list(gold_types)})

    types = []
    for kind in sorted(set(stats) | set(unresolved)):
        tp, fp, fn = stats[kind]["tp"], stats[kind]["fp"], stats[kind]["fn"]
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        types.append({
            "type": kind,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "false_positive": fp,
            "false_negative": fn,
            "unresolved_review": unresolved.get(kind, 0),
            "support": tp + fn,
        })
    return {
        "mode": "rule+AI" if use_ai else "rule-only",
        "elapsed_seconds": round(elapsed, 2),
        "cells": cell_count,
        "status_totals": dict(status_totals),
        "completeness": store.verify_completeness(),
        "types": types,
        "false_positive_cells": misses[:60] if (misses := misses) else [],
        "ai_audit": getattr(result, "ai_audit", None) or {},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", default="results/gold_accuracy.json")
    parser.add_argument("--with-ai", action="store_true")
    parser.add_argument("--key", action="append", default=[])
    args = parser.parse_args()

    out = Path(args.json)
    out.parent.mkdir(exist_ok=True)
    payloads = []
    with tempfile.TemporaryDirectory() as tmp:
        csv_path = Path(tmp) / "gold_cells.csv"
        build_csv(csv_path)
        payloads.append(run_gold(csv_path, use_ai=False, key_columns=args.key))
        if args.with_ai:
            payloads.append(run_gold(csv_path, use_ai=True, key_columns=args.key))

    out.write_text(json.dumps(payloads, ensure_ascii=False, indent=2), encoding="utf-8")
    for payload in payloads:
        print(f"\n=== {payload['mode']} ({payload['elapsed_seconds']}s, "
              f"{payload['cells']:,} cells, complete={payload['completeness']['complete']})")
        header = f"{'유형':<18}{'P':>8}{'R':>8}{'F1':>8}{'FP':>6}{'FN':>6}{'미 resolution':>14}"
        print(header)
        for row in payload["types"]:
            print(f"{row['type']:<18}{row['precision']:>8.2f}{row['recall']:>8.2f}"
                  f"{row['f1']:>8.2f}{row['false_positive']:>6}{row['false_negative']:>6}"
                  f"{row['unresolved_review']:>14}")
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
