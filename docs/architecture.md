# Privacy Scanner architecture

## 1. Analysis flow

```
CSV input
  -> format detection (encoding / delimiter / header)
  -> streaming parse (default batch 1,000 rows)
  -> column metadata + statistics
  -> rule based detection (25 detectors)
  -> protection (MASK/HASH/ENC) analysis
  -> code group analysis
  -> Local LLM보조 judgement (llama.cpp, CPU/CUDA/Metal)
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
| app/core/privacy_engine.py | detector orchestration with tag gating |
| app/detectors/*.py | one independent class per privacy type |
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

## 4. Memory and throughput

- 1,000 row batches, never a full file load, no pandas DataFrame.
- Distinct values: HyperLogLog plus a bounded exact set (2,000 entries).
- Sample values stored masked, at most 6 per column.
- 130,000 rows x 14 columns finishes in roughly 20 seconds (rules only,
  Apple Silicon, measured by tests/performance/test_large_file.py).

## 5. LLM integration

- `LocalLLMEngine.load()` loads GGUF directly through llama_cpp; no localhost
  HTTP server is started.
- GPU offload layers are planned from free VRAM (`cpu_fallback.plan_inference`);
  failures fall back to CPU, and if RAM is too low the AI step is skipped while
  rule results are still written.
- Responses are parsed and schema validated; malformed JSON is retried twice,
  then recorded as "AI 미확정".
- AI never overwrites a rule hit; the method column records 규칙 / 규칙+AI / AI(보조).

## 6. Risk model

```
score = sensitivity * (0.45 + 0.55 * ratio) * rule_confidence
verified protection (MASK/HASH/ENC)      -> -0.30
compound identification (>=3 types)      -> +0.12
```

Levels: 매우 높음 0.85+, 높음 0.65+, 보통 0.45+, 낮음 0.25+, otherwise 확인 필요.
