# Jev-Style Decision Model AI layer — implementation & verification report

측정 환경: macOS 15 (Apple Silicon, 18 cores, 64 GB RAM), Python 3.15.2, PySide6 6.12.0,
PyInstaller 6.22.3, pytest 9.2.0, numpy 2.5.5, tokenizers 0.23.3, psutil 7.2.2.
**NVIDIA GPU / CUDA 없음** → CUDA·Windows 실측은 "검증 불가"로 기록 (아래 §11).

아래 모든 수치는 실제로 실행한 명령의 산출물(`results/qa/*.json`)에서 took.

---

## 1. 변경/추가 파일과 주요 클래스

| 파일 | 역할 |
|---|---|
| [app/ai/jev/decision.py](../app/ai/jev/decision.py) | 판정 시나리오 정의: `PERSONALITY_OPTIONS`(4), `TYPE_OPTIONS`(21), `NAME_CONTEXT_OPTIONS`, `ADDRESS_OPTIONS`, `PROTECTION_OPTIONS`, `CODE_MEANING_OPTIONS`, `JEV_TYPE_TO_INTERNAL`, `JevStyleDecisionRequest` / `JevStyleDecisionResult` |
| [app/ai/jev/model_manager.py](../app/ai/jev/model_manager.py) | `JevStyleModelManager`: 로컬 GGUF discovery, GGUF header parsing, SHA256 integrity, RAM guard |
| [app/ai/jev/adapter.py](../app/ai/jev/adapter.py) | `JevStyleAdapter`: lifecycle, redaction (`redact_sample`, `shape_pattern`, `charset_profile`), `build_column_state`, `decide`, `decide_many`, `classify_column` |
| [app/ai/jev/engine.py](../app/ai/jev/engine.py) | `JevStyleEngine`: 기존 `LocalLLMEngine`과 동일한 인터페이스(load/judge_column/judge_code_group/memory_usage/summary/close) |
| [app/ai/__init__.py](../app/ai/__init__.py) | `create_ai_engine()`: verified Jev-Style 모델이 있으면 Jev, 없으면 legacy LLM engine |
| [app/core/analysis_service.py](../app/core/analysis_service.py) | engine factory 적용 + AI 판정/규칙 판정 병행, RSS watchdog(3500MB)로 AI 중단 |
| [app/core/result_aggregator.py](../app/core/result_aggregator.py) | `compare_decisions()` — 규칙 판정 vs AI 판정 vs 최종 판정(규칙 근거 우선, 불일치 시 `requires_review`) |
| [app/ui/settings_window.py](../app/ui/settings_window.py) | AI engine/모델 선택, 모델·scorer·integrity·RAM 상태 표시 |
| [app/ui/result_window.py](../app/ui/result_window.py) | 규칙 판정 / AI 판정 / 일치 / 최종 판정 / 추가_review 컬럼 |
| [app/main.py](../app/main.py) | CLI: `--ai-engine`, `--jev-model`, `--cpu-only`, `--no-ai`, `--json`에 AI 요약 |
| [tools/jev_download_models.py](../tools/jev_download_models.py) | HF 다운로드 + scorer 빌드 |
| [tools/jev_package.py](../tools/jev_package.py) | scorer self-contained화 + package manifest(revision/SHA256) |
| [tools/jev_benchmark.py](../tools/jev_benchmark.py) | 61-case 합성 데이터셋으로 Accuracy / per-type P·R·F1 |
| [tools/jev_scenarios.py](../tools/jev_scenarios.py) | 시나리오(C·D·E·F) Accuracy |
| [tools/jev_hardware.py](../tools/jev_hardware.py) | Metal/CPU × ctx 2K/4K/8K load·RSS·latency |
| [tools/jev_ram_guard.py](../tools/jev_ram_guard.py) | 8/6/4/2 GB simulated RAM에서 모델 선택/생략 |
| [tools/jev_offline_check.py](../tools/jev_offline_check.py) | socket 차단된 source 실행 |
| [tools/jev_offline_binary_check.py](../tools/jev_offline_binary_check.py) | frozen binary 실행 중 TCP connection 감시 |
| [tools/gui_check.py](../tools/gui_check.py) | offscreen GUI render + screenshot |
| [tests/qa/test_jev_ai.py](../tests/qa/test_jev_ai.py) | 12개 QA tests |

Model binaries (`*.gguf`, vendored dylib, `vendor/llama.cpp`) are gitignored;
manifests, scorer source and runtime are tracked.

---

## 2. Jev-Style runtime wiring

```
CSV -> CsvLoader -> PrivacyEngine (rules)         [unchanged]
                          |
                          v
             create_ai_engine(config, hardware)
                          |
                JevStyleEngine -> JevStyleAdapter
                          |
      importlib load of models/jev-style/<release>/jev_style_decision_gguf.py
                          |
             JevStyleDecisionGGUF(state, question, options)
                          |
        subprocess: models/jev-style/<release>/build/jev-score[-v2]
                     (JSON-lines stdin/stdout, no HTTP, no localhost server)
```

- No `requests`, `urllib`, `http.server`, or llama-server anywhere on the AI path.
- Scorer protocol is the official one: state + question + criteria dict →
  `{answer, probabilities, top_probability, input_tokens, ...}`.
- Model version and scorer are matched: 0.8B v3 → `jev-score`, 2B v4 → `jev-score-v2`.

---

## 3. 실제 실행 방법 (verified)

```bash
./.venv/bin/python tools/jev_download_models.py          # HF download + scorer build
./.venv/bin/python tools/jev_package.py                  # self-contained scorer + manifest

./.venv/bin/python -c "from app.ai import create_ai_engine; from app.ai.gpu_detector import detect_hardware; \
  from app.config.settings import AnalysisConfig; e=create_ai_engine(AnalysisConfig(jev_model='2b-v4'), detect_hardware()); \
  print(e.load(), e.summary())"
```

Example real output (2B v4, Metal):

```
True {'model': 'Jev-Style-2B-Decision-v4', 'backend': 'metal', 'load_seconds': 1.24,
      'peak_rss_mb': 2044.2, 'context_limit': 32768,
      'scorer_info': {'protocol': 'jev-score-v2', 'ngl': 999, 'n_ctx': 32768, 'ready': True}}
```

---

## 4. 모델별 Accuracy (61 합성 cases, `[tests/qa/jev_dataset.py](../tests/qa/jev_dataset.py)`)

| Metric | rule engine only | 0.8B v3 | 2B v4 |
|---|---|---|---|
| Type accuracy | 0.3115 | **0.6557** | **0.7213** |
| Macro F1 (15 types) | – | 0.636 | 0.722 |
| PERSONAL vs NON-PERSONAL accuracy | – | 0.4426 | 0.7869 |
| PERSONAL precision / recall / F1 | – | 0.500 / 0.059 / 0.105 | 0.889 / 0.706 / 0.787 |
| UNCERTAIN rate (type) | – | 1/61 | 1/61 |
| Decisions / failures | – | 144 / 0 | 144 / 0 |

Scenario questions (34 cases, `results/qa/jev_scenarios.json`):

| Scenario | 0.8B v3 | 2B v4 |
|---|---|---|
| C. 한국어 이름 문맥 (13) | 6/13 = 0.462 | **13/13 = 1.000** |
| D. 주소 판정 (6) | 3/6 = 0.500 | 5/6 = 0.833 |
| E. 컬럼 의미 (binary, 9) | 2/9 = 0.222 | 8/9 = 0.889 |
| F. 보호 컬럼 (6) | 3/6 = 0.500 | 5/6 = 0.833 |
| overall | 0.412 | 0.912 |

Main confusions (type level):
- 0.8B: `PHONE_NUMBER→NONE` ×4, `NAME→NONE` ×3, `RESIDENT_ID→NONE` ×3, `ADDRESS→NONE` ×2 — i.e. it
  under-flags sparse/masked values.
- 2B: `NONE→NAME` ×2, `NONE→CUSTOM_IDENTIFIER` ×2, `LOCATION→NONE` ×2 — i.e. it over-flags
  identifier-like columns and misses lat/lon.

`LOCATION` (lat/lon) is recall 0 for **both** models; the rule engine continues to catch it
(range check), and rule evidence is never overwritten.

---

## 5. 유형별 Precision / Recall / F1

| Type | 0.8B TP/FP/FN | P | R | F1 | 2B TP/FP/FN | P | R | F1 |
|---|---|---|---|---|---|---|---|---|
| ADDRESS | 1/0/2 | 1.000 | 0.333 | 0.500 | 3/1/0 | 0.750 | 1.000 | 0.857 |
| BANK_ACCOUNT | 1/0/0 | 1.000 | 1.000 | 1.000 | 1/0/0 | 1.000 | 1.000 | 1.000 |
| BIRTH_DATE | 2/0/0 | 1.000 | 1.000 | 1.000 | 2/1/0 | 0.667 | 1.000 | 0.800 |
| CARD_NUMBER | 2/0/0 | 1.000 | 1.000 | 1.000 | 2/0/0 | 1.000 | 1.000 | 1.000 |
| CUSTOM_IDENTIFIER | 4/0/1 | 1.000 | 0.800 | 0.889 | 3/4/2 | 0.429 | 0.600 | 0.500 |
| EMAIL | 1/1/0 | 0.500 | 1.000 | 0.667 | 1/1/0 | 0.500 | 1.000 | 0.667 |
| FOREIGNER_ID | 0/0/1 | 0.000 | 0.000 | 0.000 | 0/0/1 | 0.000 | 0.000 | 0.000 |
| IMEI | 1/0/0 | 1.000 | 1.000 | 1.000 | 1/0/0 | 1.000 | 1.000 | 1.000 |
| IMSI | 1/0/0 | 1.000 | 1.000 | 1.000 | 1/0/0 | 1.000 | 1.000 | 1.000 |
| LOCATION | 0/0/2 | 0.000 | 0.000 | 0.000 | 0/0/2 | 0.000 | 0.000 | 0.000 |
| NAME | 4/1/4 | 0.800 | 0.500 | 0.615 | 7/3/1 | 0.700 | 0.875 | 0.778 |
| NONE | 20/16/3 | 0.556 | 0.870 | 0.678 | 14/4/9 | 0.778 | 0.609 | 0.683 |
| OTHER_PERSONAL | 1/1/1 | 0.500 | 0.500 | 0.500 | 2/0/0 | 1.000 | 1.000 | 1.000 |
| PHONE_NUMBER | 1/1/4 | 0.500 | 0.200 | 0.286 | 4/1/1 | 0.800 | 0.800 | 0.800 |
| RESIDENT_ID | 1/0/3 | 1.000 | 0.250 | 0.400 | 3/1/1 | 0.750 | 0.750 | 0.750 |

---

## 6. Load & inference time (`results/qa/jev_hardware.json`)

| Model | backend | ctx | load (s) | avg latency (ms) | failures |
|---|---|---|---|---|---|
| 0.8B v3 | Metal | 2048/4096/8192 | 0.62 / 0.59 / 0.58 | 83.2 / 77.1 / 78.6 | 0 |
| 0.8B v3 | CPU | 2048/4096/8192 | 0.66 / 0.68 / 0.71 | 799.8 / 804.0 / 809.5 | 0 |
| 2B v4 | Metal | 2048/4096/8192 | 0.66 / 0.56 / 0.57 | 133.0 / 134.0 / 132.9 | 0 |
| 2B v4 | CPU | 2048/4096/8192 | 0.86 / 0.90 / 0.94 | 1409.2 / 1419.3 / 1434.1 | 0 |

Context size (2K → 8K) changes latency by <2%; context is capped by the model
(25,600 tokens for v3 head budget, 32,768 for v4).

Pipeline throughput (130,000 rows, `tests/fixtures/synthetic/large_130k.csv`):

| Mode | wall time | rows/s | AI decisions |
|---|---|---|---|
| rules only | 23.6 s | 5,509 | 0 |
| rules + 2B v4 (Metal) | 27.8 s | 4,672 | 32 |

AI adds ≈4.2 s because it runs **per column**, not per row.

---

## 7. RAM / VRAM

| Model | backend | total peak RSS (parent + scorer) | parent RSS after load | scorer RSS |
|---|---|---|---|---|
| 0.8B v3 | Metal | 1.65–1.70 GB | 259–308 MB | 1.379 GB |
| 0.8B v3 | CPU | 2.27–2.38 GB | 349–455 MB | 1.873 GB |
| 2B v4 | Metal | 2.31–2.41 GB | 509–616 MB | 1.787 GB |
| 2B v4 | CPU | 3.69–3.79 GB | 669–775 MB | 2.990 GB |

Frozen binary (`dist/PrivacyScanner`) runs: reported `peak_rss_mb` 1,488 MB (0.8B)
and 2,104 MB (2B); an independent `ps`-based sampling of the whole onefile process
tree during a 130k-row run gave 2,147 MB total peak. VRAM: Apple Silicon Metal usage
is inside the unified memory measured above; no discrete VRAM counter on this machine.

RAM guard (measured, `results/qa/jev_ram_guard.json`):

| available RAM | auto-selected | 0.8B load | 2B load |
|---|---|---|---|
| 8 GB | 0.8b-v3 | OK | OK (Metal 2.4 GB, CPU 3.7 GB) |
| 6 GB | 0.8b-v3 | OK | OK |
| 4 GB | 0.8b-v3 | OK | **refused** ("RAM 부족 (필요 ~4.1GB)") |
| 2 GB | none | **refused** | **refused** |

The gate uses measured RSS estimates (2,300 MB / 3,700 MB) + 0.5 GB headroom, not
the file size, and the analysis still completes with rule-only results.

---

## 8. CPU / Metal / CUDA

- **Apple Silicon Metal**: verified working (`ngl=999`, `system_info: MTL : EMBED_LIBRARY = 1`,
  `n_ctx 32768`, `ready: true`) for both models; 8–10× faster than CPU-only.
- **CPU fallback**: verified working for both models (no GPU code path).
- **CUDA / NVIDIA / RTX 5060**: **not tested** — no NVIDIA hardware in this environment.
  `detect_hardware()` reports `vram_total_gb = 0.0`, `cuda = ""`, backend `metal`.
  llama.cpp was built with `-DBUILD_SHARED_LIBS=ON -DLLAMA_CURL=OFF`; a CUDA build on a
  Windows/NVIDIA host is required before Windows+CUDA support can be claimed.

---

## 9. Raw-PII exposure tests

| Test | Result |
|---|---|
| `test_redaction_never_keeps_full_digits` | PASS — every AI sample keeps ≤3 digits |
| `test_shape_pattern_is_reversible_free` | PASS — `010-1234-5678` → `999-9999-9999` |
| `test_column_state_contains_no_raw_samples` | PASS — JSON state has no raw value |
| `test_engine_decision_path_with_fake_engine` | PASS — no raw name in any state |
| `test_ai_states_sent_to_model_contain_no_raw_pii` | PASS — end-to-end canary CSV: none of the 12 raw canary values reached the AI state |
| Log/CSV/TXT export leakage (`tests/qa/test_leakage.py`) | PASS (existing suite) |
| Exception paths | `adapter` catches all scorer exceptions and returns `AI 미확정`; exception text is `type: message` only, never a sample |

Example AI state actually sent (masked):

```json
{"column": "USER_NM", "value_patterns": ["K*K*K x5"], "masked_samples": ["김**", "이**"],
 "charset": {"hangul": 0.78, "digit": 0.0}, "lengths": [3, 4]}
```

---

## 10. Offline packaging

```bash
./.venv/bin/python tools/jev_package.py          # scorer rpath -> @loader_path, dylibs copied into build/
./.venv/bin/python tools/build.py --target macos --skip-tests
```

- Built artifact: **`dist/PrivacyScanner` = 1,759.0 MB** (bundled Jev-Style model+scorer data 1,825.3 MB).
  `--rule-only` flag builds a model-free binary if a lighter package is needed.
- Portable-bundle test: the whole `models/jev-style/0.8b-v3` directory was copied to `/tmp` and
  `./build/jev-score` executed there → rpath is `@loader_path`, absolute vendor path removed, decision
  returned successfully.
- Frozen-binary closed-network test (`tools/jev_offline_binary_check.py`):
  exit 0, **0 external TCP connections** over the whole process tree, AI loaded from
  `_MEIPASS/models/jev-style/...`, 30 decisions, 0 failures.
  Wall time / reported peak RSS: 21.87 s / 1,488 MB (0.8B) and 15.21 s / 2,104 MB (2B).
- Source-level offline test (`tools/jev_offline_check.py`): all `socket.connect` blocked, both
  models load and return probabilities → no HF access at runtime.
- Manifests: `models/jev-style/<release>/package_manifest.json` records repository, revision
  (`0afb4d7b…` for 0.8B v3, `3d49b5b2…` for 2B v4), quantization, per-file SHA256/size,
  llama.cpp ref `b94959a64d011915d77a5381c7d4846dcda99530`, and packaging date;
  [models/jev-style/THIRD-PARTY-NOTICES.md](../models/jev-style/THIRD-PARTY-NOTICES.md) covers licences.
- Git LFS now tracks the distributable binaries (`*.gguf`, `*.whl`, `*.dll`, `*.exe`) so the
  offline bundle can be cloned or zipped without any download at run time:
  `git lfs install && git clone https://github.com/gomin90/piiscan.git`.
- `tools/make_bundle_manifest.py` writes `manifests/offline-bundle-manifest.json` (SHA256 + size of
  every distributable file) and `--verify` re-hashes the tree; `scripts/windows/verify-offline-bundle.ps1`
  performs the same check on Windows.
- `tools/make_release_bundle.py` builds `dist/piiscan-offline-bundle.zip` (206 files, 2,255 MB) for a
  GitHub Release asset; GitHub's own "Download ZIP" only serves LFS *pointers*.

---

## 11. Not implemented / not verified

| Item | Status |
|---|---|
| Windows EXE build & run | **Not verified** — macOS-only host. `tools/build.py --target windows` refuses to cross-build on purpose. |
| CUDA / NVIDIA / RTX 5060 inference | **Not verified** — no NVIDIA hardware. |
| 8 GB RAM Windows measurement | **Not verified** — simulated gate behaviour only (see §7). |
| `llama-cpp-python` legacy LLM path | Not installed here; `auto` falls back to it only when no Jev model is present, untested in this env. |
| Multi-threaded / parallel inference | Not implemented: one scorer process, `threads=4`, concurrency = 1 as specified. |
| Threshold tuning from held-out data | Margin threshold 0.05 is only used to flag `requires_review`; no auto-threshold optimisation. |
| Windows-native scorer build | Cross-built with MinGW on macOS (`runtime/windows/jev-score.exe`, `jev-score-v2.exe`); **never executed on Windows**. |
| Windows wheelhouse install / EXE run | Wheels fetched and hash-verified, dependency closure checked (`tools/verify_wheelhouse.py`); not installed or run on Windows. |
| Git LFS push of the ~2.3 GB bundle | Depends on GitHub access; see §14. |

---

## 12. Model adoption recommendation

**Default: 2B v4 (`Jev-Style-2B-Decision-v4-GGUF`, Q4_K_M) when ≥6 GB free RAM and Metal/CUDA are
available; 0.8B v3 when RAM or latency is constrained.**

Evidence:

- Type accuracy 0.7213 vs 0.6557, macro F1 0.722 vs 0.636.
- Scenario questions are decisive for the Korean-name/address/protection cases: 0.912 vs 0.412.
  The 0.8B answers `UNCERTAIN` on 5 of 13 name-context cases — it is not usable as a
  context judge, only as a coarse type hint.
- Cost: 2B is +25 % memory (2.4 GB vs 1.7 GB Metal peak) and +50 ms per decision —
  negligible against the 0.3 s/1000-row rule engine, and both fit in 8 GB RAM.
- Both models are Apache-2.0 and run 100 % locally; neither replaces the rule engine
  (rule evidence wins in `compare_decisions`).

---

## 13. Reproducible commands

```bash
# 1. models + scorer (needs network once, dev machine only)
./.venv/bin/python tools/jev_download_models.py
./.venv/bin/python tools/jev_package.py

# 2. accuracy
./.venv/bin/python tools/jev_benchmark.py            -> results/qa/jev_benchmark.json
./.venv/bin/python tools/jev_scenarios.py            -> results/qa/jev_scenarios.json

# 3. hardware / RAM
./.venv/bin/python tools/jev_hardware.py             -> results/qa/jev_hardware.json
./.venv/bin/python tools/jev_ram_guard.py            -> results/qa/jev_ram_guard.json

# 4. offline evidence
./.venv/bin/python tools/jev_offline_check.py        -> results/qa/jev_offline_check.json
./.venv/bin/python tools/build.py --target macos --skip-tests
./.venv/bin/python tools/jev_offline_binary_check.py -> results/qa/jev_offline_binary_check.json

# 5. GUI smoke test (headless)
./.venv/bin/python tools/gui_check.py                -> results/gui_*.png

# 6. full suite
./.venv/bin/python -m pytest tests -q                # 95 passed

# 7. offline distribution artefacts (macOS side)
./.venv/bin/python tools/make_wheelhouse_lock.py     # requirements/requirements-lock.txt
./.venv/bin/python tools/verify_wheelhouse.py        # dependency closure check
./.venv/bin/python tools/make_bundle_manifest.py     # manifests/offline-bundle-manifest.json
./.venv/bin/python tools/make_bundle_manifest.py --verify
./.venv/bin/python tools/make_release_bundle.py      # dist/piiscan-offline-bundle.zip
./.venv/bin/python tools/jev_smoke.py --model 0.8b-v3 --cpu-only
```

Manual CLI check:

```bash
./dist/PrivacyScanner --file tests/fixtures/synthetic/mixed_pii.csv --mode full \
    --jev-model 2b-v4 --json   # ai.summary shows model/backend/decisions/peak RSS
```

---

## 14. Git status

- Base commit for this work: `1b3e28d` (GUI/QA baseline).
- Jev-Style layer: `3085778` ("Add Jev-Style decision-model AI layer with offline packaging"),
  then `c5d2c72` (documentation of the measured results).
- The Windows offline bundle (models, tokenizer, native Windows runtime, wheelhouse, scripts,
  manifest, licences) is committed in the follow-up commit; the large binaries are Git LFS objects,
  so `git status` after the commit is clean.
---

## 15. Windows offline bundle - what the macOS machine produced

The Windows runtime could not be executed here, so the deliverable is a
verified-on-macOS **bundle** plus the scripts the user runs on Windows.

| Artifact | How it was produced | Evidence available | Executed on Windows? |
|---|---|---|---|
| `vendor/wheels/` (31 wheels, 104.8 MB) | `pip download --platform win_amd64 --only-binary=:all: --python-version 3.12` | `requirements/requirements-lock.txt` (name, size, SHA256); `tools/verify_wheelhouse.py` proves the dependency closure | no |
| `models/jev-style/0.8b-v3` / `2b-v4` GGUF + tokenizer | `tools/jev_download_models.py` with the already-authenticated Hugging Face CLI | `manifest.json` per release (repo, revision, SHA256, size, licence) | no (loaded on macOS) |
| `runtime/windows/jev-score.exe`, `jev-score-v2.exe` | MinGW 14.0.0 cross-compile of the official `jev_score.cpp` / `jev_score_v2.cpp`, `-static`, linked against the official `llama.dll` | PE header + import table inspected with `objdump`: only non-system import is `llama.dll`; `BUILD-INFO.json` records every hash | **no** |
| `runtime/windows/llama.dll`, `ggml*.dll`, `libomp.dll` (18 files) | official llama.cpp `b12556` `bin-win-cpu-x64` asset | SHA256 in `BUILD-INFO.json` and in the bundle manifest | no |
| `manifests/offline-bundle-manifest.json` | `tools/make_bundle_manifest.py` over all git-tracked bundle files | 206 files / 2,286.2 MB; `--verify` reported 0 mismatches, 0 missing | re-verifiable with `verify-offline-bundle.ps1` |
| `scripts/windows/{diagnose,verify-offline-bundle,install-offline,build-exe,fetch-cuda-backend,build-jev-score}.ps1`, `run.bat` | hand-written; syntax-checked with PowerShell 7.7 (`Parser::ParseFile` -> 0 errors) and `diagnose.ps1` / `verify-offline-bundle.ps1` dry-run against this repo | dry-run reports in `results/windows/` | **no** (Windows-only cmdlets such as `Get-CimInstance` are guarded) |
| `docs/windows-test-guide.md`, `docs/THIRD-PARTY-NOTICES.md`, `LICENSE` | written from the recorded provenance | - | - |
| `samples/windows_sample.csv` | synthetic, no real personal data | manifest entry | no |
| `dist/piiscan-offline-bundle.zip` | `tools/make_release_bundle.py` | 2,255.1 MB, 206 files (not committed; regenerate on demand) | no |

Honest limits:

1. **No Windows execution.** DLL loading, EXE build, CPU/CUDA inference and 8 GB
   behaviour are all still unverified.
2. The scorer EXEs were built with MinGW, not MSVC. Official MSVC builds of the
   scorer are not published, so the Windows test guide provides
   `build-jev-score.ps1` for a native rebuild if the cross-built binary fails.
3. `llama.dll` requires the Visual C++ runtime (`MSVCP140.dll`, `VCRUNTIME140.dll`).
4. The official CUDA asset is built for CUDA 12.4; Blackwell (RTX 5060) support must
   be confirmed on the actual machine.
5. `llama-cpp-python` has no win_amd64 wheel, so the legacy generative LocalLLM path
   cannot be installed offline; the Jev-Style path does not need it.
