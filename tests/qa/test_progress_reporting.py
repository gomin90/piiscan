"""CLI/GUI progress reporting (spec item 9).

The checks here are deliberately independent of the analysis engine: the console
output has to be correct even when the totals are unknown, when the terminal is
redirected, or when AI never ran.
"""
from __future__ import annotations

import io
import time

from app.core.progress import (
    STAGES,
    ConsoleProgress,
    ProgressTracker,
    stage_label,
)


def _plain_tracker(**kwargs) -> tuple[ProgressTracker, io.StringIO]:
    buffer = io.StringIO()
    renderer = ConsoleProgress(buffer, plain_interval=0.0)
    tracker = ProgressTracker(sink=renderer.render, interval=0.0, **kwargs)
    return tracker, buffer


def test_all_nine_stages_have_labels_and_indexes():
    from app.core.progress import STAGE_INDEX

    expected = [
        "format", "stream", "ai_column", "ai_cell", "ai_row",
        "merge", "review", "export", "cleanup",
    ]
    assert [stage for stage, _ in STAGES] == expected
    assert len(expected) == 9
    for stage in expected:
        assert STAGE_INDEX[stage] == expected.index(stage) + 1
        assert stage_label(stage)  # every stage is printable


def test_stage_transitions_are_recorded_in_order():
    tracker, buffer = _plain_tracker(file_total=1)
    tracker.start_file(1, "a.csv", 10)
    for stage, _label in STAGES:
        tracker.stage(stage)
    output = buffer.getvalue()
    seen: list[str] = []
    for line in output.splitlines():
        if line.strip() and line not in seen:
            seen.append(line)
    indexes = [line.split("/", 1)[0] for line in seen]
    assert indexes == [str(i) for i in range(1, 10)]
    labels = [line.split(" ", 1)[1].split(" | ", 1)[0] for line in seen]
    assert labels == [label for _key, label in STAGES]


def test_eta_is_absent_until_enough_rows_are_processed():
    tracker = ProgressTracker(interval=0.0)
    tracker.start_file(1, "a.csv", 100)
    assert tracker.snapshot().eta_seconds is None
    tracker.stage("stream")
    tracker.row_progress(1, 100)
    time.sleep(0.01)
    tracker.row_progress(50, 100)
    event = tracker.snapshot()
    assert event.eta_seconds is not None
    assert event.rows_per_sec_recent > 0


def test_unknown_row_total_never_invents_a_percentage():
    tracker, buffer = _plain_tracker(file_total=1)
    tracker.start_file(1, "a.csv", 0)
    tracker.stage("stream")
    tracker.row_progress(100, 0)
    tracker.emit(force=True)
    line = buffer.getvalue()
    assert "미확인" in line
    assert "(%)" not in line


class _FakeTerminal(io.StringIO):
    """A StringIO that behaves like a TTY for the renderer."""

    def isatty(self) -> bool:
        return True


def test_interactive_console_reuses_one_line_and_plain_console_does_not():
    interactive = _FakeTerminal()
    renderer = ConsoleProgress(interactive, interval=0.0, plain_interval=0.0)
    assert renderer.interactive
    tracker = ProgressTracker(sink=renderer.render, interval=0.0, file_total=1)
    tracker.start_file(1, "a.csv", 10)
    for done in range(1, 6):
        tracker.row_progress(done, 10)
    renderer.finish()
    text = interactive.getvalue()
    assert "\r" in text
    assert "\x1b" not in text
    # 5 progress updates + completion, all on one physical line
    assert text.count("\n") == 1

    plain = io.StringIO()
    renderer = ConsoleProgress(plain, interval=0.0, plain_interval=0.0)
    assert not renderer.interactive
    tracker = ProgressTracker(sink=renderer.render, interval=0.0, file_total=1)
    tracker.start_file(1, "a.csv", 10)
    for done in range(1, 6):
        tracker.row_progress(done, 10)
    renderer.finish()
    lines = [line for line in plain.getvalue().splitlines() if line.strip()]
    assert len(lines) >= 6
    assert "\r" not in plain.getvalue()


def test_progress_line_has_no_personal_data():
    tracker, buffer = _plain_tracker(file_total=1)
    tracker.start_file(1, "customer_pii.csv", 10)
    tracker.stage("stream")
    tracker.row_progress(7, 10)
    tracker.set_review_pending(3)
    tracker.export_progress(2, 5)
    tracker.emit(force=True)
    line = buffer.getvalue()
    assert "customer_pii.csv" in line          # file name is fine
    assert "\x1b" not in line
    for token in ("010-", "john", "doe"):
        assert token not in line


def test_ai_candidate_and_judged_counts_are_kept_separate():
    tracker = ProgressTracker(interval=0.0)
    tracker.stage("ai_cell")
    tracker.ai_progress(candidates=10, judged=4, batches=2, reused=3,
                        success=3, failed=1, skipped=6)
    event = tracker.snapshot()
    assert event.ai_candidates == 10
    assert event.ai_judged == 4
    assert event.ai_reused == 3
    assert event.ai_skipped == 6
    line = tracker.snapshot().line()
    assert "AI 후보 10 / 판정 4" in line


def test_gui_fields_expose_progress_percent_for_the_worker():
    tracker = ProgressTracker(interval=0.0)
    tracker.start_file(2, "a.csv", 200)
    tracker.stage("ai_cell")
    tracker.row_progress(100, 200)
    fields = tracker.snapshot().gui_fields()
    assert fields["progress_percent"] == 50
    assert fields["현재 단계"] == stage_label("ai_cell")
    assert fields["현재 분석 파일"] == "a.csv"
    assert fields["전체 행 수"] == "200"
    assert fields["진행률"] == "50%"


def test_export_stage_reports_export_progress():
    tracker, buffer = _plain_tracker(file_total=1)
    tracker.start_file(1, "a.csv", 10)
    tracker.stage("export")
    tracker.export_progress(3, 7)
    tracker.emit(force=True)
    assert "Export 3/7" in buffer.getvalue()
