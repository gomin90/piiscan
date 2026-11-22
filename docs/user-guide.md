# USER GUIDE

## Installing

실행 파일(PrivacyScanner)을 더블 클릭하여 실행합니다. Python 설치가 필요 없습니다.

## Analyze a single file

1. "파일 분석" 탭에서 CSV 파일을 선택합니다.
2. 미리보기(20행)에서 인코딩, 구분자, 헤더 여부, 데이터 시작 행, 테이블명을 확인 또는 수정합니다.
3. "분석 실행"을 누릅니다.

## Multiple files / folder

"폴더 선택"를사용하면 folder 안의 모든 CSV가 분석됩니다.

## Result table

| Column | Meaning |
|---|---|
| 개인정보 유형 | detected privacy type |
| 탐지 건수 / 탐지 비율 | matched values and ratio |
| 보호 컬럼 | MASK / HASH / ENC columns tied to the base column |
| 분석 방식 | 규칙, 규칙+AI, AI(보조) |
| 위험도 | 매우 높음 ~ 확인 필요 |

Click a row for the column detail (statistics, masked samples, protection status).

## Reports

`results/privacy_results_<timestamp>.csv`과 `results/privacy_report_<timestamp>.txt`
generated automatically. 원본 개인정보 값은 보고서에 기록되지 않습니다.

## AI behaviour

## 판정(verdict) semantics

A column is declared 개인정보 only when the matching values dominate the column
(>= 50% of non-empty values) **and** the evidence is a confirmed pattern/checksum.
Identifier-like columns (CUST_NO, EMP_NO, ORDER_NO) whose values merely *look* like
a passport or driver licence number are reported as **부분 매칭(검토)** with the
matching rows so a human can confirm. This removes "사번 -> 여권번호" false positives.

## Input 양식 (분석 방식 / 테이블명 / 스키마 / 원본 DB)

Input is a plain CSV with a header row plus data rows (see `samples/windows_sample.csv`).
The **환경 설정** tab exposes:

| Field | Effect |
|---|---|
| 분석 방식 | `auto` (100행 이하 -> 샘플링 표시, 그 외 전수) / `sampling` (앞 N행만 분석) / `full` (전수) |
| 테이블명 / 스키마 / 원본 DB | 보고서와 결과 CSV의 metadata (기본: 파일명) |
| 행 상세 기록 | 컬럼·유형별 행 단위 evidence 최대 수 (0 = 없음) |

CLI equivalents are `--mode`, `--table`, `--schema`, `--db`, `--gpu-layers`.

## Row-level / Cell-level evidence

분석은 컬럼이 아니라 **Cell** 단위로 수행된다: 모든 Row의 모든 비어 있지 않은 Cell을
규칙 엔진이 검사하고, 각 hit은 Cell 판정(`개인정보` / `검토 필요`)과 Row 판정(개인정보
존재 여부, 근거, 위험도)으로 기록된다. 컬럼이 "개인정보 컬럼"으로 분류되지 않았더라도
Cell evidence는 삭제되지 않는다.

* 결과 탭의 **Row 단위 판정** table: 행번호 / 개인정보 존재 / 유형 / 발생 컬럼 / 위험도 /
  검토 필요.
* **Cell 상세** table: 행번호 / 컬럼 / 컬럼 인덱스 / 유형 / 판정 / 탐지값(마스킹).
* 파일 export: `results/privacy_rows_<timestamp>.csv` (Row 판정, 한 Row 한 line)와
  `results/privacy_hits_<timestamp>.csv` (Cell hit, 한 Cell 한 line).
* Row 문맥(예: 이름 + 전화번호 + 주소가 같은 Row)은 판정 근거(`판정 근거`)에 recorded.
* Ambiguous Row만 AI(Jev-Style 또는 local LLM)로 보조 판정하며, 파일당 maximum
  `max_ai_row_reviews`(default 100)번만 호출된다. 규칙 검사 Cell 수와 AI 판정 수는
  diagnostics에서 separately 집계된다.
* Detail 상한(default 5,000 Row)을 초과하면 초과 Row 수가 보고서에 표시되고
  Row/Cell count는 그대로 정확하다. 원본 값은 기록되지 않는다 (always masked).

## GPU (NVIDIA CUDA)

`detect_hardware()` probes the GPU with `nvidia-smi` (PowerShell WMI fallback on
Windows), then checks three gates before claiming GPU inference: the ggml GPU backend
library is installed next to the scorer, the CUDA runtime DLLs it imports resolve, and
the GPU's compute capability was compiled into it. Any failing gate falls back to CPU
and prints the exact reason in the 하드웨어 정보 tab, the AI status line and
`--report-json`.

For an RTX 5060 (Blackwell, sm_120) on Windows:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\diagnose.ps1
powershell -ExecutionPolicy Bypass -File scripts\windows\gpu-check.ps1
powershell -ExecutionPolicy Bypass -File scripts\windows\install-cuda-backend.ps1
powershell -ExecutionPolicy Bypass -File scripts\windows\build-jev-score.ps1 -GPU
```

`gpu-check.ps1` is the diagnostic: it lists the runtime DLLs, the CUDA DLLs
`ggml-cuda.dll` requires, the driver CUDA version, the GPU compute capability vs the
archs baked into the backend, and captures llama.cpp's stderr when the scorer tries to
load the GPU.

`--gpu-layers 0` forces CPU inference. GPU support depends on which backend binary is
installed: the CUDA 12.4 asset only carries kernels for sm_50-sm_90, so it cannot run
on Blackwell; the CUDA 13.5 asset (also vendored) carries sm_86/sm_89/sm_103/sm_120/
sm_121. `install-cuda-backend.ps1` picks the right one automatically.

## AI behaviour

GGUF 모델이 `models/` 에 있으면 Local LLM으로 컬럼 의미를 보조 판정합니다.
모델이 없거나 RAM이 부족하면 규칙 기반 결과만 출력되며프로그램는 정상 실행됩니다.
