# piiscan - Privacy Scanner

폐쇄망·air-gapped 환경에서 실행되는 독립형 개인정보(PII) 탐지 및 분석 시스템.
두 개의 독립 분석 기능이 있다: **Function A** (실제 테이블 데이터의 Row × Cell
개인정보 판정)와 **Function B** (DB 메타정보만으로 개인정보 관련 테이블/컬럼 후보
식별). 규칙 기반 엔진 + Jev-Style Decision Model(로컬 GGUF)으로 분석하고 CSV/TXT
보고서를 생성한다. 외부 API나 인터넷 연결은 사용되지 않는다.

## Quick start

```
python -m venv .venv && .venv/bin/python -m pip install -r requirements/requirements.txt
python tests/fixtures/generate_fixtures.py          # synthetic test data
python app/main.py --gui                            # pick Function A or Function B
python app/main.py --analysis table --key SEQ_NO --file tests/fixtures/synthetic/mixed_pii.csv
python app/main.py --analysis metadata --file metadata_extract.csv   # TABLE/COLUMN描述 export
python tools/jev_benchmark.py --models 0.8b-v3 2b-v4   # AI accuracy benchmark
```

Windows 오프라인 PC에서 테스트하려면 [docs/windows-test-guide.md](docs/windows-test-guide.md)
를 따라 실행 한다.

```
scripts\windows\diagnose.ps1                    # hardware + bundle report
scripts\windows\gpu-check.ps1                   # why GPU was detected but CPU inference used
scripts\windows\verify-offline-bundle.ps1       # SHA256 manifest check
scripts\windows\install-offline.ps1             # pip install from vendor\wheels
scripts\windows\run.bat --file samples\windows_sample.csv --debug
```

## CLI

Two separate analyses. Pick one; the inputs, pipeline and reports are different.

```
# Function A: real table data -> per-Cell verdicts (KEY columns are chosen by the user)
python app/main.py --analysis table --file a.csv --key SEQ_NO --key RECORD_KEY --table users --schema sales

# Function B: DB metadata (TABLE/COLUMN descriptions) -> which columns look personal
python app/main.py --analysis metadata --file metadata.csv --field-map table_name=TBL_NAME

# Resume a cancelled run from its checkpoint
python app/main.py --analysis table --file a.csv --key SEQ_NO --detail-store run.sqlite --resume
python app/main.py --folder /data/csv --no-ai --debug
```

Options: `--encoding`, `--delimiter`, `--header`, `--batch-size`, `--mode
(auto|sampling|full)`, `--resume`, `--no-ai`, `--cpu-only`, `--jev-model`, `--debug`,
`--json`, `--report-json <path>`, `--gui`, plus the detail/AI controls `--detail-store`,
`--max-detail-rows`, `--max-detail-cells`, `--max-ai-row-reviews`, `--key <col>`
(repeatable), `--field-map ROLE=HEADER`, `--max-ai-column-reviews`,
`--ai-column-reviews (selective|all|none)`, `--max-ai-cell-reviews`,
`--ai-cell-reviews (selective|all|none)`, `--ai-cell-batch-size`, `--no-cell-report`,
`--max-field-chars`, `--gui-cell-page-size`.

## Features

- Auto detection of encoding (UTF-8, UTF-8 BOM, CP949, EUC-KR, UTF-16) and
  delimiter (comma, semicolon, TAB, pipe); headerless files get `COL_001` names.
- 46 detectors covering 36 PII types, driven by the policy file
  `resources/rules/privacy_kr_2026.json`: 주민등록번호(체크섬), 여권, 운전면허, 외국인등록, SSN,
  국가보험, 카드번호(Luhn), 은행계좌(체크섬), 위도/경도, 이름, 이메일, 전화번호, 상세주소,
  생년월일, IMSI, IMEI, 지문/홍채 후보, `.sec`/`.id`/wildcard 사용자 정의 ID,
  SN/UUID/MAC/IP/Postcode(낮은 우선순위), 인증 credentials, 고객 연결식별 ID, Cell ID, RFID,
  wireless network, FAX, payment info, personal attribute, GAID, GUID, masked values -
  plus 9 *interior* scanners that look inside long JSON / Base64 / log-text Cells
  (resident id, phone, card, email, passport, birth date, MAC, coordinates, device id)
  and a free-text name scanner, so personal data buried in a big field is still found. Column-name affinity, negative
  patterns and masked-value guards are applied before a Cell can be cleared as
  non-personal, and a Cell containing several personal values reports each occurrence
  with its character offset.
- Cell-level PII verdicts: every Cell of every Row × Column gets a status
  (개인정보 / 검토 필요 / 비개인정보 / 미평가 / 빈 셀 / KEY 제외) with type,
  masked value, confidence, evidence and decision method. `privacy_cells.csv`
  contains one row per source Cell, and Row/Column summaries are aggregated from
  those Cell verdicts rather than replacing them. The completeness invariant
  (logical Cells == verdict Cells == status total) is verified per run.
- Confirmed rule hits are never suppressed by a column being classified
  non-privacy, and unconfirmed candidates are never written as confirmed types.
- Every Cell/Row verdict goes to a SQLite detail store ("Layout D": a status
  byte per Row plus sparse detail for Cells that actually carry a verdict), so
  30M Cells stay in ~130 MB RSS. The GUI reads it with keyset pagination, never
  a full-table load.
- HMAC-scoped PII grouping: identical values are grouped by type + normalised
  value + HMAC fingerprint with occurrence counts and distinct record counts.
  Raw personal values are never stored.
- AI is a per-Cell judgement (batched Jev-Style requests with a reuse cache keyed
  on value + column meaning + context), not a per-column guess. Unresolved AI
  output stays `미평가` / `검토 필요`, never silently "non-personal".
- MASK/HASH/ENC column relationship analysis with value-level verification.
- Code-group (GROUP_CODE/CODE/CODE_NAME) semantic analysis.
- Jev-Style Decision Model (0.8B v3 / 2B v4, GGUF) 로컬 판정 엔진: State+Question+
  fixed options → 선택지별 확률. 규칙 결과와 separately 저장되며 AI 실패 시 규칙 결과만
  사용한다. RAM이 부족하면 AI 자동 생략된다.
- Local GGUF inference through llama.cpp with CUDA/Metal/CPU auto selection and
  automatic CPU fallback; the rule result is always produced even if AI fails.
- Streaming parse: 150,000 rows x 20-200 columns (30,000,000 Cells) analysed with
  ~95 MB peak RSS (measured on Apple Silicon); HyperLogLog distinct estimation,
  no pandas DataFrame, 1,000-row batches, keyset-paged GUI reads (200 rows/page
  at page 14,999 of 3M Cells in 0.018 s).
- Result export: CSV rows per column/type and a human-readable TXT report;
  raw personal values are never written to reports or logs.

## Tests

```
python -m pytest tests -q                     # 299 tests (unit, integration, QA, performance)
python tools/gold_accuracy.py --with-ai       # Cell-level gold set: P/R/F1 per type
python tools/scale_benchmark.py --rows 150000 --columns 50 --key SEQ_NO
python tools/metadata_benchmark.py --rows 5000
python tools/qa_accuracy.py                   # rule-engine accuracy per PII type
python tools/jev_benchmark.py                 # Jev-Style accuracy per PII type
python tools/jev_scenarios.py                 # name / address / protected-column scenarios
python tools/jev_ram_guard.py                 # 8/6/4/2 GB RAM gate behaviour
python tools/jev_offline_binary_check.py      # frozen binary: 0 TCP connections
python tools/jev_smoke.py --model 0.8b-v3    # one real decision per scenario
python tools/verify_wheelhouse.py            # offline dependency closure
python tools/make_bundle_manifest.py         # SHA256 manifest of the bundle
python tools/make_release_bundle.py          # dist/piiscan-offline-bundle.zip
```

## Layout

```
app/{core,detectors,ai,ui,export,utils,config}
app/metadata_analysis/                Function B: field mapping, classification, reports
models/jev-style/{0.8b-v3,2b-v4}/     GGUF + tokenizer + scorer source + manifests
runtime/windows/                      jev-score.exe, jev-score-v2.exe, llama.cpp DLLs
vendor/wheels/                        offline win_amd64 wheelhouse (35 wheels, 129 MB)
resources/rules                     user-configurable custom patterns
scripts/windows/                    diagnose / verify / install / build / run
tools/                                build.py, qa_*, jev_*, benchmark.py,
                                      scale_benchmark / metadata_benchmark / gold_accuracy
docs/                               architecture, user guide, offline build,
                                      windows test guide, Jev-Style report,
                                      two-mode-analysis-design.md (design + measurements)
manifests/                          offline-bundle-manifest.json (SHA256 of every file)
samples/                            synthetic CSV for smoke tests
```

Large binaries (GGUF models, wheels, DLLs) are tracked with Git LFS. To clone the
offline bundle:

```
git lfs install
git clone https://github.com/gomin90/piiscan.git
```

GitHub's "Download ZIP" only serves LFS **pointers**, and the whole bundle is 2.50 GB
(above GitHub's 2 GiB per-asset limit), so the Release assets are split. Download all
three and unzip them into one directory:

```
piiscan-app.zip          771.3 MB   code, native runtime, CUDA 13.5 DLLs, wheelhouse, scripts, docs, manifest
piiscan-model-0.8b.zip   508.6 MB   0.8B v3 GGUF + tokenizer
piiscan-model-2b.zip    1219.1 MB   2B v4 GGUF + tokenizer
```

Published at [release v0.7.1](https://github.com/gomin90/piiscan/releases/tag/v0.7.1).
Change history per version is in [CHANGELOG.md](CHANGELOG.md).
Build them yourself with `python tools/make_release_bundle.py --split`, then check integrity with
`scripts/windows/verify-offline-bundle.ps1`.

See [docs/architecture.md](docs/architecture.md) and
[docs/offline-build.md](docs/offline-build.md).
