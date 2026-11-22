"""사람이 읽기 쉬운 TXT 보고서. 원본 개인정보 값은 포함하지 않는다."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app import __version__
from app.utils.masking import mask_value

RISK_ORDER = {"매우 높음": 0, "높음": 1, "보통": 2, "낮음": 3, "확인 필요": 4}


def write_txt(file_results: list, output: str | Path) -> str:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append("=" * 70)
    lines.append("개인정보 탐지 분석 보고서 (Privacy Scanner)")
    lines.append(f"버전: {__version__}")
    lines.append(f"생성일시: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 70)

    total_rows = sum(result.rows_parsed for result in file_results)
    total_cells = sum(result.cell_counts.get("cells_inspected", 0) for result in file_results)
    pii_rows = sum(len(result.row_records) for result in file_results)
    pii_columns = sum(1 for result in file_results for r in result.results)
    high_risk = sum(1 for result in file_results for r in result.results if r.risk_level in ("매우 높음", "높음"))
    lines.append("")
    lines.append("[전체 요약]")
    lines.append(f"  분석 파일 수: {len(file_results)}")
    lines.append(f"  분석 행 수: {total_rows:,}")
    lines.append(f"  전수 검사 Cell 수: {total_cells:,}")
    lines.append(f"  개인정보 Cell이 발견된 Row 수: {pii_rows:,}")
    lines.append(f"  개인정보 컬럼-유형 항목: {pii_columns}")
    lines.append(f"  위험도 높음 및 확인 필요 항목: {high_risk}")
    lines.append("")

    for result in file_results:
        lines.append("-" * 70)
        lines.append(f"파일: {Path(result.path).name}")
        lines.append(f"  테이블: {result.settings.get('table', '')} / 스키마: {result.settings.get('schema', '') or '없음'}")
        lines.append(f"  인코딩: {result.settings.get('encoding')} | 구분자: {repr(result.settings.get('delimiter'))} | 헤더: {result.settings.get('header')}")
        lines.append(f"  분석 방식: {result.settings.get('mode')} | 분석 행: {result.rows_parsed:,} (오류 {result.rows_error}, 빈 행 {result.rows_empty})")
        lines.append(f"  소요 시간: {result.elapsed_seconds:.2f}s | AI: {result.ai_summary.get('status', '미사용')}")
        counts = result.cell_counts
        lines.append(
            f"  Row/Cell 검사: Row {counts.get('rows_inspected', 0):,} / Cell {counts.get('cells_inspected', 0):,} "
            f"(빈 Cell {counts.get('empty_cells', 0):,}, 규칙 호출 {counts.get('detector_calls', 0):,}, "
            f"Cell hit {counts.get('cell_hits', 0):,})"
        )
        if result.row_records:
            lines.append(f"  [Row 단위 판정] 개인정보 포함 Row {len(result.row_records)}건")
            for record in result.row_records[:20]:
                types = ", ".join(record.pii_types + record.review_types)
                where = ", ".join(f"{cell.column}(#{cell.column_index})" for cell in record.cells[:5])
                lines.append(
                    f"    Row {record.row}: {'개인정보' if record.has_pii else '없음'} | {types} | "
                    f"{where} | 근거: {record.basis} | 신뢰도 {record.confidence:.2f} | "
                    f"위험도 {record.risk_level} | {'검토 필요' if record.needs_review else '확정'}"
                )
            if len(result.row_records) > 20:
                lines.append(f"    ... 나머지 {len(result.row_records) - 20}건은 privacy_rows CSV 참조")
        elif counts.get("detail_truncated_rows"):
            lines.append(
                f"  Row 상세 상한({counts.get('detail_limit', 0)}) 초과: {counts['detail_truncated_rows']} Row 생략 "
                "(Row/Cell 수는 그대로 집계됨)"
            )
        if result.protection:
            lines.append("  [보호(MASK/HASH/ENC) 컬럼]")
            for group in result.protection:
                data = group.as_dict()
                parts = []
                for key in ("mask", "hash", "enc"):
                    if data.get(key):
                        parts.append(f"{key}={data[key]['column']}({data[key]['evidence'][0]})")
                lines.append(f"    {data['base']}: {', '.join(parts) if parts else '보호 컬럼 없음'} -> {data['status']}")
        if result.code_groups:
            lines.append("  [코드 그룹 분석]")
            for group in result.code_groups:
                data = group.as_dict()
                lines.append(
                    f"    {data['group_column'] or '(그룹 없음)'} / {data['code_column']} / "
                    f"{data['desc_column'] or '(설명 없음)'} -> "
                    f"개인정보 관련: {'Y' if data['privacy_relevant'] else 'N'} "
                    f"(근거: {data['note'] or '규칙 기반'}, 신뢰도 {data['confidence']})"
                )
        lines.append("  [컬럼 결과]")
        if not result.results:
            lines.append("    탐지된 개인정보 없음")
        for row in sorted(result.results, key=lambda r: (RISK_ORDER.get(r.risk_level, 9), -r.hits)):
            lines.append(
                f"    {row.column:<22} {row.display_name:<14} 건수 {row.hits:>6} "
                f"비율 {row.ratio:>6.2%} 위험도 {row.risk_level:<8} 방식 {row.method} "
                f"근거: {row.rationale[:120]}"
            )
            if row.hit_rows:
                detail = ", ".join(f"행{h['row']}={h['value']}" for h in row.hit_rows[:10])
                lines.append(f"      탐지행 {row.hits}건 중 예시: {detail}")
        review = [r for r in result.results if r.requires_review]
        if review:
            lines.append("  [추가 확인 사항]")
            for row in review:
                lines.append(f"    - {row.column} ({row.display_name}): 값 검증 또는 AI 판단 불확실")
    lines.append("")
    lines.append("=" * 70)
    lines.append("주의: 보고서에는 마스킹/통계 정보만 포함되며 원본 개인정보 값은 기록되지 않습니다.")
    output.write_text("\n".join(lines), encoding="utf-8")
    return str(output)
