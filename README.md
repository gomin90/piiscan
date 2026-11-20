# piiscan - Privacy Scanner

폐쇄망·air-gapped 환경에서 실행되는 독립형 개인정보(PII) 탐지 및 분석 시스템.
CSV 파일을 규칙 기반 엔진 + Jev-Style Decision Model(로컬 GGUF)으로 분석하고 CSV/TXT
보고서를 생성한다. 외부 API나 인터넷 연결은 사용되지 않는다.

## Quick start

```
python -m venv .venv && .venv/bin/python -m pip install -r requirements/requirements.txt
python tests/fixtures/generate_fixtures.py          # synthetic test data
python app/main.py --file tests/fixtures/synthetic/mixed_pii.csv --output-dir results
python app/main.py --gui                            # PySide6 desktop UI
python tools/jev_benchmark.py --models 0.8b-v3 2b-v4   # AI accuracy benchmark
```

Windows 오프라인 PC에서 테스트하려면 [docs/windows-test-guide.md](docs/windows-test-guide.md)
를 따라 실행 한다.

```
scripts\windows\diagnose.ps1                    # hardware + bundle report
scripts\windows\verify-offline-bundle.ps1       # SHA256 manifest check
scripts\windows\install-offline.ps1             # pip install from vendor\wheels
scripts\windows\run.bat --file samples\windows_sample.csv --debug
```

## CLI

```
python app/main.py --file a.csv --file b.csv --mode full --table users --schema sales
python app/main.py --folder /data/csv --no-ai --debug
```

Options: `--encoding`, `--delimiter`, `--header`, `--batch-size`, `--mode
(auto|sampling|full)`, `--no-ai`, `--cpu-only`, `--jev-model`, `--debug`, `--json`,
`--report-json <path>`, `--gui`.

## Features

- Auto detection of encoding (UTF-8, UTF-8 BOM, CP949, EUC-KR, UTF-16) and
  delimiter (comma, semicolon, TAB, pipe); headerless files get `COL_001` names.
- 25 detectors: 주민등록번호(체크섬), 여권, 운전면허, 외국인등록, SSN, 국가보험,
  카드번호(Luhn), 은행계좌(체크섬), 위도/경도, 이름, 이메일, 전화번호, 상세주소,
  생년월일, IMSI, IMEI, 지문/홍채 후보, `.sec`/`.id`/wildcard 사용자 정의 ID,
  SN/UUID/MAC/IP/Postcode(낮은 우선순위).
- MASK/HASH/ENC column relationship analysis with value-level verification.
- Code-group (GROUP_CODE/CODE/CODE_NAME) semantic analysis.
- Jev-Style Decision Model (0.8B v3 / 2B v4, GGUF) 로컬 판정 엔진: State+Question+
  fixed options → 선택지별 확률. 규칙 결과와 separately 저장되며 AI 실패 시 규칙 결과만
  사용한다. RAM이 부족하면 AI автоматически 생략된다.
- Local GGUF inference through llama.cpp with CUDA/Metal/CPU auto selection and
  automatic CPU fallback; the rule result is always produced even if AI fails.
- Streaming parse: 130,000+ rows analysed in ~1GB-bounded memory (HyperLogLog
  distinct estimation, no pandas DataFrame, 1,000-row batches).
- Result export: CSV rows per column/type and a human-readable TXT report;
  raw personal values are never written to reports or logs.

## Tests

```
python -m pytest tests -q                     # 95 tests (unit, integration, QA)
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
models/jev-style/{0.8b-v3,2b-v4}/   GGUF + tokenizer + scorer source + manifests
runtime/windows/                    jev-score.exe, jev-score-v2.exe, llama.cpp DLLs
vendor/wheels/                      offline win_amd64 wheelhouse (31 wheels, 105 MB)
resources/rules                     user-configurable custom patterns
scripts/windows/                    diagnose / verify / install / build / run
tools/                              build.py, qa_*, jev_*, benchmark.py
docs/                               architecture, user guide, offline build,
                                    windows test guide, Jev-Style report
manifests/                          offline-bundle-manifest.json (SHA256 of every file)
samples/                            synthetic CSV for smoke tests
```

Large binaries (GGUF models, wheels, DLLs) are tracked with Git LFS. To clone the
offline bundle:

```
git lfs install
git clone https://github.com/gomin90/piiscan.git
```

See [docs/architecture.md](docs/architecture.md) and
[docs/offline-build.md](docs/offline-build.md).
