# Privacy Scanner 변경 이력

The single source of the version number is `app/__init__.py` (`__version__`).
 릴리스 산출물(split bundle)과 manifest는 각 릴리스 시점의 소스로 재생성된다.

## v0.7.1 (2026-11-25)

Personal-data coverage, precision and AI-latency release. No change to the
Function A / Function B separation, the Layout D store or the report schema.

### 탐지 정확도
- **Policy layer added**: `resources/rules/privacy_kr_2026.json` (34 PII type
  definitions, column-name aliases, negative patterns, context-class rules) is
  loaded by `app/config/privacy_policy.py` and consumed by
  `app/detectors/context_policy.py` (`ScanContext` + `find_matches()`).
  46 detectors / 36 labelled types are now exercised by the gold set.
- **Masked values can never be cleared as non-personal.** `rule_negative_verdict()`
  returns no clearance when `looks_masked()` is true, which removed the
  `CARD_NUMBER` false positive (precision 0.9688 → 1.0000) and keeps every masked
  Cell in the review queue instead of silently dismissing it.
- **Column-affinity short circuit**: a detector whose column name has no affinity
  with the type and whose value shape is not strong no longer runs its full regex
  battery, cutting rule-only analysis cost without changing verdicts.
- Gold-set measurement (1,972 labelled Cells, 122,688 Cell grid):
  precision **1.0000**, recall **0.9957**, F1 **0.9978**, FP **0**, FN **5**
  (4 masked Cells that stay `검토 필요`). Rule-only and rule+AI give the same FP/FN.

### 다중 occurrence / 위치 보고
- A single Cell containing several personal values now records **every** occurrence
  with its character offset: `cell_info.spans` = `TYPE@start-end;TYPE@start-end`,
  `privacy_cells.csv` gains `match_position`, and the summary reports
  `multi_occurrence_cells` / `extra_occurrences`.
- The phone detector returns one Match per number inside a merged remark Cell
  (`PHONE_NUMBER@4-17;PHONE_NUMBER@21-34`) instead of one whole-cell match.

### AI 판정 성능
- **Function B metadata AI reuse cache**: identical column signatures (column, data
  type, comment, length, PK, FK) are inferred once and reused. 5,000 Row metadata
  with AI went from **201.86 s** (2,811 inferences) to **3.6 s** (12 inferences,
  2,799 reuses) with the same P/R/F1 (1.00) and mapping accuracy (11/11).
- **Function A Cell AI batching** sends one question per distinct reuse key instead
  of one per Cell, and the audit now separates `attempted` from `reused`.
- AI backend detection fixed: `app/ai/scorer_resolver.py` includes
  `runtime/{windows,macos,linux}` and every `models/jev-style/*/build` directory,
  so Metal is used on Apple Silicon instead of falling back to a ~10× slower path.

### 안정성
- Temporary detail stores are swept before every run (`sweep_orphans()`) and
  registered/unregistered consistently, so an aborted process cannot accumulate
  `piiscan-detail-*` directories. After the full suite and the 30M Cell benchmark
  **0** temporary stores remained.
- Windows `gpu-check.ps1`: `-WindowStyle Hidden` and `-NoWindow` are mutually
  exclusive (the probe now starts without a window-style argument and falls back to
  a direct call); CUDA 13.3 toolkits are detected and routed to
  `install-cuda-backend.ps1 -CudaVersion 13.3`.
- Performance budgets are enforced by `tests/performance/`: 130,000 Row streaming
  analysis < 120 s and the 1,000 × 1,000 (1M Cell) scenario < 30 s.
- `CustomIdentifierDetector` builds its rule tuples once in `__init__` instead of
  re-reading rule dicts, re-lowercasing exclude suffixes and rescanning the value
  for letters on every Cell; the 1M Cell scenario went from 30.9 s to 27.1 s with
  identical verdict counts.

### 실측 (v0.7.1, macOS Apple Silicon)
| scenario | logical Cells | Cell/s | peak RSS | store |
|---|---:|---:|---:|---:|
| Function A 150k × 20 | 3,000,000 | 18,620 | 88.9 MB | 1,459.4 MB |
| Function A 150k × 50 | 7,500,000 | 15,265 | 91.8 MB | 4,140.1 MB |
| Function A 150k × 100 | 15,000,000 | 14,247 | 113.5 MB | 8,280.2 MB |
| Function A 150k × 200 | 30,000,000 | 14,547 | 158.8 MB | 17,315.7 MB |
| Function B metadata 5,000 rows (rule+AI) | — | 3.6 s total | ~300 MB | 3.14 MB |

All seven completeness invariants pass in every scenario. Full pytest suite:
see the release notes; the 1 skipped test is the deliberate-crash teardown control
(`PIISCAN_TEARDOWN_CONTROL=1`).

## v0.7.0 (2026-11-24)

- Function A (table Cell analysis) and Function B (DB metadata analysis) split into
  independent pipelines, UI screens and CLI modes (`--analysis table|metadata`).
- Cell-level verdicts with six states (개인정보 / 검토 필요 / 비개인정보 / 미평가 /
  빈 셀 / KEY 제외) and the completeness invariant
  (logical Cells == verdict Cells == status total).
- SQLite "Layout D" detail store, streaming CSV/TXT export, keyset-paged GUI reads.
- HMAC-scoped PII entity grouping and Record / Group / Cell reports.
- Per-Cell AI judgement with batched Jev-Style requests and a reuse cache.
- Cancel / resume checkpoints, QThread lifecycle, temporary store cleanup and the
  SIGTERM deadlock fix (`threading.RLock` in `DetailStore`).
- User-specified single/composite KEY columns excluded from analysis.
- CUDA 13.3 / RTX 5060 support scripts for Windows.

## v0.6.0 and earlier

- v0.6.0: review findings from v0.5.0 fixed (selective AI column policy, detail store
  caps, order-independent verdicts).
- v0.5.0: SQLite detail store, offline bundle manifest, offline wheelhouse.
- v0.4.0: row-level Cell evidence, AI row review.
- v0.3.0–v0.4.x: Jev-Style local GGUF decision engine, RAM guard, offline binaries.
- v0.1–v0.2: rule engine, encoding detection, GUI and CLI reports.
