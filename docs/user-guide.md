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

* 결과 탭의 **Row 단위 판정** table: 행번호 / 개인정보 상태(확정·검토 필요·없음) /
  확정 개인정보 / 검토 필요 개인정보 / 발생 컬럼 / 위험도 / AI Row 판정.
* **Cell 상세** table: 행번호 / 컬럼 / 컬럼 인덱스 / 유형 / 판정 / 탐지값(마스킹).
  Row를 클릭하면 그 Row의 Cell 상세만 로드되며, 한 화면에 기본 200 Cell
  (`gui_cell_page_size`, CLI `--gui-cell-page-size`)만 렌더링한다. Cell 페이지
  buttons(`이전/다음 Cell 페이지`)로 전체 Cell 상세를 SQLite에서 계속 조회할 수 있다.
* Row 상세는 SQLite detail store에 전량 저장되며 GUI는 페이지(기본 200 Row)로 조회한다.
  `--detail-store <path>`로 저장 파일을 지정할 수 있고, 저장 건수는 GUI progress pane와
  TXT 보고서("상세 저장: Row N / Cell M")에서 확인할 수 있다. 지정하지면 파일별 임시 DB가
  생성되고 CLI는 Export 완료 후, GUI는 프로그램 종료 시 임시 DB(WAL/SHM 포함)를 삭제한다.
  명시적으로 경로 지정하면 보존된다.
* 여러 파일을 같은 `--detail-store`에 분석하면 파일별로 `analysis_id`로 분리되어 같은
  Row 번호라도 Results 섞이지 않는다.
* 긴 JSON/Base64/log Cell 안의 개인정보도 interior detector로 검사되며, 저장되는 값은
  매칭된 fragment만 마스킹되어 저장된다. CSV field 한도는 `--max-field-chars`
  (default 10,000,000)이며 초과 field는 parse error로 집계된다(implicitly discarded 아님).
* 파일 export: `results/privacy_rows_<timestamp>.csv` (Row 판정, 한 Row 한 line)와
  `results/privacy_hits_<timestamp>.csv` (Cell hit, 한 Cell 한 line).
* Row 문맥(예: 이름 + 전화번호 + 주소가 같은 Row)은 판정 근거(`판정 근거`)에 recorded.
* Ambiguous Row만 AI(Jev-Style 또는 local LLM)로 보조 판정하며, 파일당 maximum
  `max_ai_row_reviews`(default 100)번만 호출된다. 규칙 검사 Cell 수와 AI 판정 수는
  diagnostics에서 separately 집계된다.
* Detail 상한(default 5,000 Row)을 초과하면 초과 Row 수가 보고서에 표시되고
  Row/Cell count는 그대로 정확하다. 원본 값은 기록되지 않는다 (always masked).

## 분석 범위 제한 (max_rows / max_cols)

`max_rows`(default 999,999)와 `max_cols`(default 999,999)는 **상한**이지 사전 할당
크기가 아니다. 파일이 더 작으면 실제 Row/Col까지만 분석하고, 상한을 초과한 Row/Col은
"미검사"로 집계된다.

* CLI `--max-rows`, `--max-cols` (0 = 무제한), GUI 설정 창 "최대 분석 Row / Col".
* 헤더 없는 CSV, 헤더보다 긴 Row(EXTRA_COL_n)도 실제 width 기준으로 Col 수를 파악한다.
* 잘린 Row/Col은 TXT 보고서에 "분석 범위: Row 200 / 파일 Row 500 (미검사 Row 300)"와
  "경고: ... 이 결과는 전체 파일 전수 검사가 아니다" line로 표시되고, GUI progress pane의
  `분석 범위` field에도 같은 text가 나온다. diagnostics에는
  `analysis_range_truncated` event가 recorded된다.
* Resume: `max_rows`는 그 분석 실행의 **전체** Row 상한이다. Checkpoint에 40 Row가
  이미 analysed라면 `--max-rows 100` resume 시 추가 100 Row가 아니라 Row 41~100만
  분석한다. 파일/스키마/max_cols가 checkpoint와 다르면 Resume이 안전하지 않으므로
  checkpoint를 reset하고 처음부터 다시 분석한다(`resume_scope_mismatch`).

## 진행률 표시 (CLI + GUI)

CLI와 GUI는 같은 `ProgressTracker` events를 사용한다. 9 단계:
파일 형식/크기 확인 → CSV 스트리밍 + Rule Engine → AI Column Review → AI Cell Review
→ AI Row Review → Rule/AI 판정 병합 → Human Review 대상 생성 → 결과 Export →
임시 자원 정리.

한 줄에는 `n/9 단계 | i/N 파일 | Row done/total (%) rows/s | Cell ... | AI 후보/판정
(batch, reuse, 성공, 실패, 생략) | Human Review 대기 | Export k/m | etime | ETA`가
나온다.

* Interactive terminal: `\r`로 같은 줄 갱신. ANSI escape를 사용하지 않아 CMD/PowerShell
  모두에서 동일하게 동작한다. Redirect/pipe then: 주기별 plain newline log lines.
* `--progress auto|plain|none`, `--progress-interval <초>` (default 0.25).
* `--json` then: JSON은 stdout, 진행 line은 stderr.
* 전체 Row 수를 알 수 없then percentage를 만들어 표시하지 않고 "전체 Row 수 미확인"으로
  표시한다. ETA는 충분히 처리된 after에만 나온다.
* AI 후보 수와 실제 추론 수는 separate counters이며 progress line에 구분 printed된다.
* 원문 개인정보는 진행 로그에 절대 출력되지 않는다.

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
