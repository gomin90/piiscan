# Privacy Scanner 독립 QA / 폐쇄망 배포 검증 보고서

기준 commit: `9346f51`. 아래 모든 수치는 해당 커밋 위에 QA harness를 추가한 뒤 **실제로 실행**하여 얻었다.
실행하지 않은 항목은 "검증 불가"로 표시했으며, 테스트하지 않은 항목은 통과로 기록하지 않았다.

## 0. 검증 방법과 산출물

| 산출물 | 역할 |
|---|---|
| [tests/qa/dataset.py](../tests/qa/dataset.py) | ground truth 합성 데이터셋. 정답은 Luhn·주민번호 checksum·MCC 등 1차 원리 계산으로 생성 (검증 대상 코드에 의존하지 않음) |
| [tests/qa/metrics.py](../tests/qa/metrics.py) | 유형별 P/R/F1/FP/FN (detector level + pipeline level) |
| [tools/qa_accuracy.py](../tools/qa_accuracy.py) | `results/qa_accuracy_before.json` / `results/qa_accuracy_after.json` |
| [tools/qa_hardware.py](../tools/qa_hardware.py) | 5개 hardware 시나리오 + 130,000행 실측 → `results/qa_hardware.json` |
| [tools/qa_offline.py](../tools/qa_offline.py) | network static scan + socket 차단 실행 + frozen binary → `results/qa_offline.json` |

```bash
./.venv/bin/python -m pytest tests -q                              # 80 passed (26.1 s)
./.venv/bin/python tools/qa_accuracy.py --json results/qa_accuracy_after.json
./.venv/bin/python tools/qa_hardware.py
./.venv/bin/python tools/qa_offline.py
```

측정 환경: macOS 15, Apple Silicon 18 cores, 64 GB RAM, **NVIDIA GPU 없음**, Python 3.15,
PySide6 6.12.0, psutil 7.2.2, PyInstaller 6.22.3, pytest 9.2.0, **llama-cpp-python 없음**.

---

## 1. 명세 대비 구현률

| 명세 항목 | 상태 | 근거 (코드 위치) |
|---|---|---|
| CSV 인코딩·구분자·헤더 자동 감지 | 완료 | [detect_format()](../app/core/csv_detector.py#L144) |
| 1,000행 batch streaming (전 파일 load 없음) | 완료 | [iter_rows()](../app/core/csv_loader.py#L107), 130,000행 peak RSS 34.8 MB |
| 컬럼 통계 (HLL distinct, length bucket, masking ratio, masked sample ≤6) | 완료 | [ColumnStats.record()](../app/core/column_stats.py#L52) |
| 25개 independent detector | **부분** | [build_detectors()](../app/core/privacy_engine.py#L45) → **24개** (LOCATION이 위도/경도 통합). `docs/architecture.md`의 "25 detectors"와 불일치 |
| MASK/HASH/ENC 보호 컬럼 그룹핑 + 값 검증 | 완료(defect 수정 후) | [analyze_protection()](../app/core/encryption_analyzer.py#L105) |
| GROUP/CODE/DESC 코드 그룹 분석 | 부분 | [analyze()](../app/core/code_group_analyzer.py#L110). GGUF 모델이 없어 최종 판정은 `decision="pending"` |
| 위험도 5단계 점수 | 완료 | [assess_column()](../app/core/risk_engine.py#L22) |
| AI가 규칙 결과를 덮지 않음 | 완료 | `app/core/result_aggregator.py`, pipeline QA로 확인 |
| Local LLM (llama.cpp) 추론 | **미구현 / 검증 불가** | [load()](../app/ai/llm_manager.py#L100) 존재. GGUF binary 없음 + llama-cpp-python 미설치 → 실제 추론 **0회** |
| CUDA/Metal offload | 부분 | [plan_inference()](../app/ai/cpu_fallback.py#L30) 계획만. 실제 offload 실측 불가 |
| `resources/prompts/` 프롬프트 파일 | **미구현** | directory 비어 있음. 프롬프트는 `app/ai/prompt_manager.py`에 hardcoded |
| CSV/TXT 보고서 export | 완료 | [write_csv()](../app/export/csv_exporter.py#L14), [write_txt()](../app/export/txt_exporter.py#L12) |
| PySide6 GUI 7 tabs | 완료(수정 후) | [run_analysis()](../app/ui/main_window.py#L91) — 수정 전: `SettingsWindow.apply()` 미 호출, debug tab 항상 비어 있음 |
| CLI | 완료 | `app/main.py`, `tests/integration/test_pipeline.py` |
| Windows EXE | **검증 불가** | macOS host. PyInstaller cross-build 불가 |
| macOS 실행 파일 | 완료 | `dist/PrivacyScanner` (Mach-O arm64, 35.9 MB) |
| `vendor/wheels/` 오프라인 wheel bundle | **미구현** | directory 없음 → `docs/offline-build.md`의 `pip install --no-index` 실행 불가 |
| GGUF 모델 bundle + SHA256 manifest | 부분 | 검증 코드 존재. manifest sha256 = `REPLACE_WITH_OFFLINE_MODEL_SHA256`(placeholder), GGUF 없음 |
| benchmark 도구 | 완료 | 20,000행 3.42 s / 5,855 rows/s |
| 모델 load 전 temp directory writable check (`docs/build-guide.md`) | **수정 후 완료** | 이전: 코드 없음. `load()`에 probe write 추가 + test |
| `config.threads` 실제 반영 | **수정 후 완료** | 이전: `max(1, min(4, cpu_count))`로 user setting 무시 |
| DB 분석 (--db/--table/--schema) | 부분 | `table`·`db_type`는 metadata로만 전달. 실제 DB connector 없음 |
| 130,000행 ≈ 20초 | 실측 22.3 s | 20열, 5,825 rows/s |
| 원문 개인정보 recording 금지 | 완료 | `tests/qa/test_leakage.py` 6 pass |

**구현률: 25 항목 중 완료 16 · 부분 4 · 미구현 3 · 검증 불가 2 → 약 64% 완전 구현.**

---

## 2. 개인정보 유형별 탐지 정확도

170개 합성 값(positive 70 + lookalike/negative 100). `before` = `9346f51`, `after` = 7장 defect 수정 후.

| 유형 | P before | R before | F1 before | FP | FN | P after | R after | F1 after | FP | FN |
|---|---|---|---|---|---|---|---|---|---|---|
| ADDRESS | 0.333 | 0.750 | 0.462 | 6 | 1 | 1.000 | 1.000 | 1.000 | 0 | 0 |
| BANK_ACCOUNT | 0.143 | 0.333 | 0.200 | 6 | 2 | 1.000 | 0.667 | 0.800 | 0 | 1 |
| BIRTH_DATE | 1.000 | 1.000 | 1.000 | 0 | 0 | 1.000 | 1.000 | 1.000 | 0 | 0 |
| CARD_NUMBER | 0.571 | 0.800 | 0.667 | 3 | 1 | 0.625 | 1.000 | 0.769 | 3 | 0 |
| CUSTOM_ID_SUFFIX | 1.000 | 1.000 | 1.000 | 0 | 0 | 1.000 | 1.000 | 1.000 | 0 | 0 |
| CUSTOM_SEC_ID | 1.000 | 1.000 | 1.000 | 0 | 0 | 1.000 | 1.000 | 1.000 | 0 | 0 |
| CUSTOM_WILDCARD_ID | 0.250 | 1.000 | 0.400 | 3 | 0 | 1.000 | 1.000 | 1.000 | 0 | 0 |
| DRIVER_LICENSE | 0.429 | 1.000 | 0.600 | 4 | 0 | 1.000 | 1.000 | 1.000 | 0 | 0 |
| EMAIL | 1.000 | 1.000 | 1.000 | 0 | 0 | 1.000 | 1.000 | 1.000 | 0 | 0 |
| FOREIGNER_ID | 0.250 | 1.000 | 0.400 | 3 | 0 | 1.000 | 1.000 | 1.000 | 0 | 0 |
| IMEI | 1.000 | 1.000 | 1.000 | 0 | 0 | 1.000 | 1.000 | 1.000 | 0 | 0 |
| IMSI | 0.500 | 1.000 | 0.667 | 2 | 0 | 0.667 | 1.000 | 0.800 | 1 | 0 |
| INSURANCE_NUMBER | 0.500 | 1.000 | 0.667 | 3 | 0 | 1.000 | 0.667 | 0.800 | 0 | 1 |
| IP | 0.600 | 1.000 | 0.750 | 2 | 0 | 1.000 | 1.000 | 1.000 | 0 | 0 |
| LOCATION (위도/경도) | 0.750 | 1.000 | 0.857 | 1 | 0 | 1.000 | 0.667 | 0.800 | 0 | 1 |
| MAC | 1.000 | 1.000 | 1.000 | 0 | 0 | 1.000 | 1.000 | 1.000 | 0 | 0 |
| NAME | 0.714 | 1.000 | 0.833 | 2 | 0 | 1.000 | 1.000 | 1.000 | 0 | 0 |
| PASSPORT | 0.667 | 1.000 | 0.800 | 2 | 0 | 0.800 | 1.000 | 0.889 | 1 | 0 |
| PHONE_NUMBER | 1.000 | 1.000 | 1.000 | 0 | 0 | 1.000 | 1.000 | 1.000 | 0 | 0 |
| POSTCODE | 0.600 | 1.000 | 0.750 | 2 | 0 | 1.000 | 1.000 | 1.000 | 0 | 0 |
| RESIDENT_ID | 1.000 | 1.000 | 1.000 | 0 | 0 | 1.000 | 1.000 | 1.000 | 0 | 0 |
| SN | 0.286 | 0.667 | 0.400 | 5 | 1 | 1.000 | 0.667 | 0.800 | 0 | 1 |
| SOCIAL_SECURITY | 1.000 | 1.000 | 1.000 | 0 | 0 | 1.000 | 1.000 | 1.000 | 0 | 0 |
| UUID | 1.000 | 1.000 | 1.000 | 0 | 0 | 1.000 | 1.000 | 1.000 | 0 | 0 |
| **합계** | **0.596** | **0.929** | **0.726** | **44** | **5** | **0.930** | **0.943** | **0.936** | **5** | **4** |

### 남은 false positive (5)

| 값 | 오 탐지 유형 | 원인 |
|---|---|---|
| `450123456789012` (IMSI) | CARD_NUMBER | 15자·Luhn 통과 → IMSI와 카드 번호 값 공간 inherent overlap |
| card-shaped 15자 numeric 3건 | CARD_NUMBER | column hint 없으면 15자 numeric space 진정으로 ambiguous |
| `A123456789` | PASSPORT | "1자 + 9자" 여권 형식과 generic alphanumeric ID 동일 |

### 남은 false negative (4) — documented trade-off

| 값 | 미 탐지 | 이유 |
|---|---|---|
| `123-4567-****` | BANK_ACCOUNT | masking + separator 혼합 형식 |
| `730520654321` | INSURANCE_NUMBER | bare 12자 → FOREIGNER_ID 먼저 claim |
| `37.5665` | LOCATION | 4-decimal은 SN/postcode와 구분 불가. column hint 있을 때만 detection |
| `SN-1234-5678` | SN | hint 없이 noise filter에서 drop |

**설계 결정:** 값 alone ambiguous한 12자(외국인/보험/계좌), 15자(IMSI/IMEI/카드), 4-decimal 좌표는
column affinity(≥ 0.35) evidence가 있을 때만 report. harness는 hint 있는/없는 case를 모두 측정한다.

### 컬럼명–값 모순 테스트

`NAME`에 좌표, `PHONE`에 카드번호 등 12 pair를 dataset에 포함. after 기준 모순 컬럼에서
`reported_on_other_columns: []` (residual: `MIXED_NOISE`에서 BANK_ACCOUNT 1건).

### 보호 컬럼 (after)

| base | MASK | HASH | ENC | 상태 |
|---|---|---|---|---|
| USER_NAME | USER_NAME_MASK (masked ratio 1.0) | - | USER_NAME_ENC | 부분 보호 (0.5) |
| USER_ID_HASH | - | USER_ID_HASH (hex verified) | - | 부분 보호 (0.5) |
| PHONE_SHA256 | - | PHONE_SHA256 (hex verified) | - | 부분 보호 (0.5) |

수정 전: 두 hash-only 그룹이 **"보호 컬럼 없음" (score 0.0)**으로 reported → defect 확인·수정.

---

## 3. 원문 유출 검증 (실제 실행 · 전부 pass)

canary 원문: `900101-1234567`, `010-1234-5678`, `min.kim@example.com`, `1234-5678-9012-3456`, `R12345678`

| 항목 | 방법 | 결과 |
|---|---|---|
| stdout·logger | canary fixture 분석 후 log capture | pass (원문 0건) |
| 결과 CSV/TXT | export 전체 grep | pass |
| `--debug` diagnostics | events JSON grep | pass (masked sample만) |
| LLM 프롬프트 | `build_prompt()` 실제 실제 반환된 text capture | pass (`010-****-5678` 형태만) |
| 예외·오류 경로 | malformed CSV, binary file, parse error | pass (traceback에 원문 없음) |
| AI 예외 시 규칙 결과 보존 | raising fake Llama injection | pass |
| GUI export | offscreen Qt 실행 후 `results/*` grep | pass |

---

## 4. AI 추론·실패 모드 (실제 실행)

| 상황 | 실행 결과 |
|---|---|
| GGUF 모델 없음 | status="AI 비활성화 (오프라인 의미 분석 사용)", 규칙 결과 intact |
| manifest SHA256 mismatch | AI 차단 + status에 무결성 실패 message (new test) |
| llama-cpp-python 미설치 | heuristic fallback, 규칙 결과 intact |
| CUDA 초기화 실패 | exception branch → CPU retry(`n_gpu_layers=0`, 2nd call 확인), fallback_used=True |
| RAM 부족 (model + 0.8 GB) | new RAM guard: AI skip, 필요/available GB report |
| `config.threads` | plan에 반영(threads=6 → 6), physical core 상한 |
| malformed JSON | 2회 attempt 후 None("AI 미확정") — retry path 확인 |
| 실제 1.5B GGUF load/추론 속도·메모리 | **검증 불가** (binary·라이브러리 없음) |

---

## 5. 하드웨어 실측

**실측 (macOS Apple Silicon)**

| 항목 | 값 |
|---|---|
| probe | Darwin, arm, 18 cores, 64 GB total / 4.5–5.4 GB free, backend=metal, VRAM 0 |
| 130,000행 × 20열 | 22.316 s · 5,825 rows/s · RSS 26.1 → 34.8 MB |
| frozen binary | 35.9 MB, 2.66 s, peak RSS 53.5 MB, network connections 0 |
| model load time | 측정 불가 (GGUF 없음) |

**시뮬레이션(`HardwareReport` stub → `plan_inference`). 실제 hardware 측정값이 아님:**

| 시나리오 | plan backend | allow_ai | 상태 |
|---|---|---|---|
| Windows 8 GB / CPU only | cpu | false (모델 없음) | 8 GB에서 1.5B load **미실측** |
| Windows 8 GB / NVIDIA 4 GB VRAM | cpu (VRAM 부족) | false | partial offload 계산 unit test로 확인 |
| Windows RTX 5060 (free 15 GB) | cuda full offload | - | 실제 offload **미실측** |
| macOS Apple Silicon CPU | cpu | heuristic | 규칙 engine 5,825 rows/s 실측 |
| macOS Metal | metal plan | - | llama.cpp 없어 추론 불가 |

`plan_inference` branch와 probe 자체는 test으로 확인. Windows·NVIDIA 메모리/속도 수치는 **미실측**.

---

## 6. 오프라인 / 폐쇄망 배포

| 항목 | 방법 | 결과 |
|---|---|---|
| network call 없음 | 57개 app 파일 static scan(`requests`/`urllib`/`socket`/`http`) | **0건** |
| network 차단 source 실행 | `socket.socket` raising stub으로 patch → `analyse_file` | exit 0, 보고서 2건 |
| frozen binary 실행 | PyInstaller one-file + psutil connection monitor | exit 0, `network_connections: []` |
| frozen vs source parity | 28 유형 · 18 high risk · 29 CSV line identical | pass |
| GGUF bundle | `models/` scan | **gguf_present: false** |
| `vendor/wheels/` | directory check | **present: false** |
| Windows EXE | - | **검증 불가** |
| Python 없이 실행 | frozen binary (mac arm64) | pass |
| custom rules frozen 포함 | SEC_ID / ID_SUFFIX / WILDCARD 탐지 | pass |

주의: PyInstaller 자체는이 session에서 **network로 설치**됨(6.22.3). `requirements/requirements-lock.txt` pin(6.11.1)과 differ → true air-gapped build 전에 wheel bundle preparation 필요.

---

## 7. 발견된 defect과 수정 (before/after)

| # | `9346f51` defect | 수정 | 검증 |
|---|---|---|---|
| 1 | `tools/build.py`: `PyInstaller.__main__.main` 없음 / `--noconfirm` typo / `--add-data` double colon | `run`, correct flag | build exit 0 → `dist/PrivacyScanner` 생성 |
| 2 | orphan `*_HASH` 그룹 "보호 컬럼 없음" | `encryption_analyzer`에서 group populate | score 0.0 → 0.5 |
| 3 | code group desc 컬럼이 privacy 컬럼(`USER_NAME`) 선택 | `_pick_desc_column` + privacy_columns exclusion | before: 3 groups privacy_relevant=True → after: desc=None, pending |
| 4 | IMSI와 IMEI/카드 conflict | 15자 Luhn valid → None, MCC based confidence | IMSI FP 2 → 1 |
| 5 | BANK_ACCOUNT phone/NHI FP | `OTHER_FORM_RE` reject + affinity ≥ 0.35 | P 0.143 → 1.000 |
| 6 | wildcard ID file extension/TLD FP | `exclude_suffixes` rules JSON + `match()` 적용 | P 0.250 → 1.000 |
| 7 | 4-decimal false positive coordinate | hint 없으면 ≥5 decimals | P 0.750 → 1.000 |
| 8 | `LocalLLMEngine.memory_usage()` system-wide virtual memory | process RSS | RAM guard 정확 |
| 9 | GUI settings apply 미 호출 / diagnostics 비어 있음 | `run_analysis()`에서 apply + Diagnostics 전달 | headless test: batch 250·threads 4 반영 |
| 10 | `config.threads` 무시 | threads argument wire | test 추가 |
| 11 | temp dir writable check 문서만 존재 | load() 전 probe write | test: unwritable → AI 차단 |
| 12 | detector comments·main.py mojibake | cleanup | CJK sweep clean (legitimate pattern만 remaining) |

전체 suite: **80 tests pass** (기존 27 + QA 53).

---

## 8. 미해결 · 운영 제약

1. **GGUF model binary 없음** → model load time·추론 속도·model memory 모두 미실측.
2. **llama-cpp-python 미설치** → llama.cpp path is 실행d only through fake-module tests.
3. **vendor/wheels 없음** → `docs/offline-build.md` 절차 그대로 실행 불가.
4. **resources/prompts 비어 있음** → 문서와 실제 프롬프트 위치 differ.
5. **Windows / NVIDIA / RTX 5060 실측 불가** (mac host). Windows EXE build도 불가.
6. `requirements-lock.txt` pin과 installed version differ (PySide6 6.12 vs 6.7.2, pytest 9.2 vs 8.3.3, psutil 7.2 vs 5.9.8).
7. 15자(IMSI/IMEI/PAN)·12자(외국인/보험/계좌) ambiguity는 domain rule 또는 LLM assistance 필요.
8. code group 최종 판정 `pending` → AI 없으면 위험도에 반영되지 않음.
9. DB(--db/--table/--schema)는 label만 저장. 실제 DB 조회 기능 없음.
