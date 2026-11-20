"""GUI 무헤드(offscreen) 실행 검증 + 스크린샷.

QT_QPA_PLATFORM=offscreen으로 SetWindow을 렌더링하고 PNG로 저장한다.
AI 관련 필드가 실제값으로 채워졌는지 위젯 텍스트로 확인한다.

    python tools/gui_check.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ai import create_ai_engine  # noqa: E402
from app.ai.gpu_detector import detect_hardware  # noqa: E402
from app.config.settings import AnalysisConfig  # noqa: E402
from app.core.analysis_service import analyse_file  # noqa: E402
from app.ui.analysis_window import AnalysisWindow  # noqa: E402
from app.ui.result_window import ResultWindow  # noqa: E402
from app.ui.settings_window import SettingsWindow  # noqa: E402


def widget_texts(window) -> dict:
    labels = getattr(window, "labels", None)
    if isinstance(labels, dict):
        return {key: label.text() for key, label in labels.items()}
    status = getattr(window, "ai_status", None)
    return {"ai_status": status.text()} if status is not None else {}


def main() -> int:
    app = QApplication(sys.argv)
    config = AnalysisConfig()
    hardware = detect_hardware()
    results = ROOT / "results"
    results.mkdir(parents=True, exist_ok=True)

    settings = SettingsWindow(config, hardware)
    settings.show()
    settings.refresh_ai_status()
    print("settings:", widget_texts(settings))
    settings.grab().save(str(results / "gui_settings.png"))

    analysis = AnalysisWindow(hardware)
    analysis.show()
    analysis.grab().save(str(results / "gui_analysis.png"))

    engine = create_ai_engine(config, hardware)
    file_result = analyse_file("tests/fixtures/synthetic/mixed_pii.csv", config, engine)
    analysis.update_state(
        **{
            "처리 행 수": file_result.rows_parsed,
            "전체 행 수": file_result.total_rows,
            "AI 실행 상태": file_result.ai_summary.get("status", "-"),
            "추론 방식": file_result.ai_summary.get("backend", "-"),
            "AI 모델": file_result.ai_summary.get("model") or "없음",
            "AI 판정 수": str(file_result.ai_summary.get("decisions", 0)),
            "AI 생략/오류": file_result.ai_summary.get("skipped_reason")
            or file_result.ai_summary.get("error") or "-",
        }
    )
    analysis.grab().save(str(results / "gui_analysis.png"))
    print("analysis:", widget_texts(analysis))

    rows = [row for row in file_result.results]
    result_window = ResultWindow()
    result_window.show_results(rows)
    result_window.grab().save(str(results / "gui_result.png"))
    table = getattr(result_window, "table", None)
    if table is not None:
        print("result columns:", table.columnCount(), "rows:", table.rowCount())
        print("header:", [table.horizontalHeaderItem(i).text() for i in range(table.columnCount())])
        ai_column = [i for i in range(table.columnCount())
                     if table.horizontalHeaderItem(i).text() == "AI 판정"]
        if ai_column:
            print("AI 판정 sample:", [table.item(r, ai_column[0]).text() for r in range(min(5, table.rowCount()))])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
