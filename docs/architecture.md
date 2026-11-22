# Privacy Scanner architecture

## 1. Analysis flow

```
CSV input
  -> format detection (encoding / delimiter / header)
  -> streaming parse (default batch 1,000 rows)
  -> column metadata + statistics
  -> rule based detection (24 detectors; LOCATION covers 위도/경도)
     every non-empty cell of every row, cell verdict: 개인정보 / 검토 필요 / 형식 유사(무시)
  -> row context analysis (row_analysis.py): has_pii, hit cells, basis, risk
  -> protection (MASK/HASH/ENC) analysis
  -> code group analysis
  -> Local LLM보조 judgement (llama.cpp, CPU/CUDA/Metal), ambiguous rows only
  -> merge + risk scoring
  -> UI display / CSV+TXT export
```

Principle: the rule engine scans every row; the LLM only receives column
statistics and masked samples. Raw personal data is never sent to the model.

## 2. Modules

| Module | Responsibility |
|---|---|
| app/core/csv_detector.py | encoding, delimiter, header detection (bounded 256 KB head read) |
| app/core/csv_loader.py | lazy row streaming, per-row error isolation |
| app/core/column_stats.py | length buckets, HyperLogLog distinct, masked ratio |
| app/core/privacy_engine.py | detector orchestration with tag gating, per-cell scan and hit ledger |
| app/core/row_analysis.py | cell verdict (`개인정보` / `검토 필요` / `형식 유사(무시)`) and Row-level context verdict |
| app/detectors/*.py | one independent detector class per privacy type (24 registered) |
| app/core/encryption_analyzer.py | base column vs _MASK/_HASH/_ENC relation, value verification |
| app/core/code_group_analyzer.py | GROUP_CODE / CODE / CODE_NAME meaning |
| app/core/risk_engine.py | sensitivity, ratio, protection, compound identity |
| app/core/result_aggregator.py | rule + AI merge into one row per column/type |
| app/ai/* | model discovery, GPU probe, fallback plan, prompt, validator |
| app/export/* | CSV and TXT report (no raw values) |
| app/ui/* | PySide6 desktop UI, Korean labels |

## 3. Detector contract

```
class Detector:
    type_key: str                 # stable identifier
    display_name: str             # Korean label
    priority: int                 # 1 = highest, 2 = lower priority
    sensitivity: float            # 0..1 privacy sensitivity
    candidate_tags: frozenset     # cheap value tags used for gating

    def match(self, value, profile, column_name="") -> Match | None
    def column_affinity(self, column_name) -> float
```

Adding a privacy type only requires a new detector class registered in
`build_detectors()`; the analysis engine itself is untouched.

## 4. Row-level / Cell-level evidence

The engine reports **where** personal data is, not just which column looked like it.

* Every non-empty Cell of every Row is checked (`process_batch()` iterates row ×
  cell and calls only the detectors whose `candidate_tags` match the value profile).
* Each hit gets a verdict from `cell_decision()`:
  * `개인정보` - detector confirmed and adjusted confidence ≥ 0.70;
  * `검토 필요` - confidence ≥ 0.60 with column support (support ratio ≥ 0.3 or
    column-name affinity ≥ 0.35), or support ratio ≥ 0.5 with confidence ≥ 0.45;
  * `형식 유사(무시)` - everything else. It is still counted, but never reported.
* Column classification only **adjusts** confidence (support ratio ≥ 0.8 adds 0.06;
  ratio < 0.2 with no confirmation subtracts 0.10). It can never delete a Cell hit.
* `analyse_row()` combines the Cells of one Row: direct identifiers, identifier pairs
  (이름+전화, 이름+생년월일, 이름+주소, ...), and multi-type combinations give the Row
  a verdict, basis string, confidence and risk level.
* Verdicts are decided **during streaming** using the running support ratio, so the
  counters (`rows_inspected`, `cells_inspected`, `pii_cells`, `rows_with_hits`) are
  exact even when the detail ledger is capped at `max_detail_rows` (5,000 Rows).
* Only Rows with `needs_review` go to the AI, capped at `max_ai_row_reviews` (100)
  per file; rule-checked Cell count and AI judgement count are recorded separately.

## 5. Memory and throughput

- 1,000 row batches, never a full file load, no pandas DataFrame.
- Distinct values: HyperLogLog plus a bounded exact set (2,000 entries).
- Sample values stored masked, at most 6 per column.
- 130,000 rows x 14 columns finishes in roughly 20 seconds (rules only,
  Apple Silicon, measured by tests/performance/test_large_file.py).

## 6. LLM integration

- `LocalLLMEngine.load()` loads GGUF directly through llama_cpp; no localhost
  HTTP server is started.
- GPU offload layers are planned from free VRAM (`cpu_fallback.plan_inference`);
  failures fall back to CPU, and if RAM is too low the AI step is skipped while
  rule results are still written.
- Responses are parsed and schema validated; malformed JSON is retried twice,
  then recorded as "AI 미확정".
- AI never overwrites a rule hit; the method column records 규칙 / 규칙+AI / AI(보조).

## 7. Risk model

```
score = sensitivity * (0.45 + 0.55 * ratio) * rule_confidence
verified protection (MASK/HASH/ENC)      -> -0.30
compound identification (>=3 types)      -> +0.12
```

Levels: 매우 높음 0.85+, 높음 0.65+, 보통 0.45+, 낮음 0.25+, otherwise 확인 필요.
