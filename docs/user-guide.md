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

GGUF 모델이 `models/` 에 있으면 Local LLM으로 컬럼 의미를 보조 판정합니다.
모델이 없거나 RAM이 부족하면 규칙 기반 결과만 출력되며프로그램는 정상 실행됩니다.
