# piiscan - Privacy Scanner

폐쇄망·air-gapped 환경에서 실행되는 독립형 개인정보(PII) 탐지 및 분석 시스템.
CSV 파일을 규칙 기반 엔진 + 내장 Local LLM(llama.cpp)으로 분석하고 CSV/TXT 보고서를
생성한다. 외부 API나 인터넷 연결은 사용되지 않는다.

## Quick start

```
python -m venv .venv && .venv/bin/python -m pip install -r requirements/requirements.txt
python tests/fixtures/generate_fixtures.py          # synthetic test data
python app/main.py --file tests/fixtures/synthetic/mixed_pii.csv --output-dir results
python app/main.py --gui                            # PySide6 desktop UI
python tools/benchmark.py --rows 130000            # accuracy + throughput benchmark
```

## CLI

```
python app/main.py --file a.csv --file b.csv --mode full --table users --schema sales
python app/main.py --folder /data/csv --no-ai --debug
```

Options: `--encoding`, `--delimiter`, `--header`, `--batch-size`, `--mode
(auto|sampling|full)`, `--no-ai`, `--debug`, `--json`, `--gui`.

## Features

- Auto detection of encoding (UTF-8, UTF-8 BOM, CP949, EUC-KR, UTF-16) and
  delimiter (comma, semicolon, TAB, pipe); headerless files get `COL_001` names.
- 25 detectors: 주민등록번호(체크섬), 여권, 운전면허, 외국인등록, SSN, 국가보험,
  카드번호(Luhn), 은행계좌(체크섬), 위도/경도, 이름, 이메일, 전화번호, 상세주소,
  생년월일, IMSI, IMEI, 지문/홍채 후보, `.sec`/`.id`/wildcard 사용자 정의 ID,
  SN/UUID/MAC/IP/Postcode(낮은 우선순위).
- MASK/HASH/ENC column relationship analysis with value-level verification.
- Code-group (GROUP_CODE/CODE/CODE_NAME) semantic analysis.
- Local GGUF inference through llama.cpp with CUDA/Metal/CPU auto selection and
  automatic CPU fallback; the rule result is always produced even if AI fails.
- Streaming parse: 130,000+ rows analysed in ~1GB-bounded memory (HyperLogLog
  distinct estimation, no pandas DataFrame, 1,000-row batches).
- Result export: CSV rows per column/type and a human-readable TXT report;
  raw personal values are never written to reports or logs.

## Tests

```
python -m pytest tests/unit tests/integration   # 22 tests, ~3s
python -m pytest tests                          # includes the 130k-row streaming test
```

## Layout

```
app/{core,detectors,ai,ui,export,utils,config}
models/          GGUF + model_manifest.json (SHA256)
resources/rules  user-configurable custom patterns
tools/           build.py, benchmark.py
docs/            architecture, build guide, offline build, user guide
```

See [docs/architecture.md](docs/architecture.md) and
[docs/offline-build.md](docs/offline-build.md).
