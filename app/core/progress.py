"""Shared progress model for the CLI and the GUI.

Both front-ends consume the *same* events, so a progress line shown in a terminal
and the GUI panel are computed from one set of counters. Nothing here knows about
Qt or about a terminal: the tracker counts, and a sink (Qt signal, console writer,
test collector) renders.

No raw personal data is ever carried in an event - only counters, file names and
stage names.
"""
from __future__ import annotations

import sys
import time
from collections import deque
from dataclasses import dataclass, field

# The nine pipeline stages, in execution order. `stage_total` is always len(STAGES).
STAGES: list[tuple[str, str]] = [
    ("format", "파일 형식/크기 확인"),
    ("stream", "CSV 스트리밍 + Rule Engine"),
    ("ai_column", "AI Column Review"),
    ("ai_cell", "AI Cell Review"),
    ("ai_row", "AI Row Review"),
    ("merge", "Rule/AI 판정 병합"),
    ("review", "Human Review 대상 생성"),
    ("export", "결과 Export"),
    ("cleanup", "임시 자원 정리"),
]

STAGE_TOTAL = len(STAGES)
STAGE_INDEX = {key: number for number, (key, _) in enumerate(STAGES, start=1)}


def stage_label(stage: str) -> str:
    """"8/9 결과 및 보고서 Export" style label usable by both front-ends."""
    number = STAGE_INDEX.get(stage, 1)
    return f"{number}/{STAGE_TOTAL} {STAGES[number - 1][1]}"


@dataclass
class ProgressEvent:
    """One snapshot of a running analysis. Counts are exact, never estimates."""

    stage: str = "format"
    file_index: int = 1
    file_total: int = 1
    file_name: str = ""
    rows_total: int | None = None      # analysable rows (bounded by max_rows)
    rows_done: int = 0
    rows_error: int = 0
    rows_empty: int = 0
    rows_unparsed: int = 0
    cells_done: int = 0
    elapsed: float = 0.0
    stage_elapsed: float = 0.0
    rows_per_sec: float | None = None          # whole-file average
    rows_per_sec_recent: float | None = None   # last refresh window
    cells_per_sec: float | None = None
    eta_seconds: float | None = None
    ai_candidates: int = 0
    ai_judged: int = 0
    ai_batches: int = 0
    ai_reused: int = 0
    ai_success: int = 0
    ai_failed: int = 0
    ai_skipped: int = 0
    review_pending: int = 0
    export_done: int = 0
    export_total: int = 0
    scope: dict = field(default_factory=dict)  # analysis range, incl. anything excluded

    def percent(self) -> int | None:
        """Row progress; None when the denominator is genuinely unknown."""
        if not self.rows_total:
            return None
        return min(100, int(self.rows_done * 100 / self.rows_total))

    def bar(self, width: int = 24) -> str:
        percent = self.percent()
        if percent is None:
            return "[" + "-" * width + "]"
        filled = percent * width // 100
        return "[" + "#" * filled + "-" * (width - filled) + "]"

    @staticmethod
    def _fmt(value: float | None) -> str:
        if value is None:
            return "-"
        if value < 10:
            return f"{value:.1f}"
        return f"{value:,.0f}"

    @staticmethod
    def _fmt_seconds(seconds: float | None) -> str:
        if seconds is None:
            return "-"
        seconds = int(seconds)
        if seconds < 60:
            return f"{seconds}s"
        return f"{seconds // 60}m{seconds % 60:02d}s"

    def line(self) -> str:
        """Compact, terminal friendly one-liner (no ANSI, no personal data)."""
        parts = [
            stage_label(self.stage),
            f"{self.file_index}/{self.file_total} {self.file_name}",
        ]
        if self.rows_total:
            parts.append(
                f"Row {self.rows_done:,}/{self.rows_total:,} ({self.percent()}%) "
                f"{self._fmt(self.rows_per_sec_recent)} rows/s"
            )
        else:
            parts.append(f"Row {self.rows_done:,} (전체 Row 수 미확인)")
        if self.cells_done:
            parts.append(f"Cell {self.cells_done:,} ({self._fmt(self.cells_per_sec)}/s)")
        if self.ai_candidates or self.ai_judged:
            parts.append(
                f"AI 후보 {self.ai_candidates:,} / 판정 {self.ai_judged:,} "
                f"(batch {self.ai_batches:,}, reuse {self.ai_reused:,}, "
                f"성공 {self.ai_success:,}, 실패 {self.ai_failed:,}, 생략 {self.ai_skipped:,})"
            )
        if self.review_pending:
            parts.append(f"Human Review 대기 {self.review_pending:,}")
        if self.stage == "export":
            parts.append(f"Export {self.export_done}/{self.export_total}")
        parts.append(f"etime {self._fmt_seconds(self.elapsed)}")
        if self.eta_seconds is not None:
            parts.append(f"ETA {self._fmt_seconds(self.eta_seconds)}")
        return " | ".join(parts)

    def stage_index(self) -> int:
        return STAGE_INDEX.get(self.stage, 1)

    def gui_fields(self) -> dict:
        """Keys the GUI progress panel already understands, plus the new ones."""
        percent = self.percent()
        return {
            "현재 분석 파일": self.file_name,
            "전체 파일 수": self.file_total,
            "파일 진행": f"{self.file_index}/{self.file_total}",
            "현재 단계": stage_label(self.stage),
            "처리 행 수": f"{self.rows_done:,}",
            "전체 행 수": f"{self.rows_total:,}" if self.rows_total else "미확인",
            "처리 Cell 수": f"{self.cells_done:,}",
            "처리 속도": f"{self._fmt(self.rows_per_sec_recent)} row/s, {self._fmt(self.cells_per_sec)} cell/s",
            "경과 시간": self._fmt_seconds(self.elapsed),
            "예상 잔여 시간(ETA)": self._fmt_seconds(self.eta_seconds),
            "AI 후보": f"{self.ai_candidates:,}",
            "AI 추론 횟수": f"{self.ai_judged:,}",
            "AI batch": f"{self.ai_batches:,}",
            "AI Reuse": f"{self.ai_reused:,}",
            "AI 성공/실패/불가": f"{self.ai_success:,} / {self.ai_failed:,} / {self.ai_skipped:,}",
            "Human Review 대기": f"{self.review_pending:,}",
            "Export 진행": f"{self.export_done}/{self.export_total}",
            "분석 범위": self.scope.get("text", "-"),
            "AI 진행률": (
                f"{min(100, self.ai_judged * 100 // max(1, self.ai_candidates))}%"
                if self.ai_candidates else "-"
            ),
            "진행률": f"{percent}%" if percent is not None else "단계만 표시",
            "progress_percent": percent,
        }


class ProgressTracker:
    """Count work for a run of one or more files and emit snapshots to a sink.

    `emit` is throttled so a 10 million cell scan does not spend its time in
    formatting; callers that care about throughput thresholds report unconditionally
    and the tracker still decides when the sink actually sees an event.
    """

    def __init__(self, sink=None, interval: float = 0.25, file_total: int = 1) -> None:
        self.sink = sink
        self.interval = max(0.0, interval)
        self.file_total = max(1, file_total)
        self.run_started = time.perf_counter()
        self.file_index = 0
        self.file_name = ""
        self.file_started = self.run_started
        self.stage_name = "format"
        self.stage_started = self.run_started
        self.rows_total: int | None = None
        self.rows_done = 0
        self.rows_error = 0
        self.rows_empty = 0
        self.rows_unparsed = 0
        self.cells_done = 0
        self.ai_candidates = 0
        self.ai_judged = 0
        self.ai_batches = 0
        self.ai_reused = 0
        self.ai_success = 0
        self.ai_failed = 0
        self.ai_skipped = 0
        self.review_pending = 0
        self.export_done = 0
        self.export_total = 0
        self.scope: dict = {}
        self.last_emit = 0.0
        self.last_rows = 0
        self.last_cells = 0
        self.last_emit_time = self.run_started
        # Kept for tests and for `--debug` reports; bounded so a 10 million row
        # run does not accumulate snapshots.
        self.events: deque[ProgressEvent] = deque(maxlen=200)

    # ------------------------------------------------------------------ lifecycle
    def start_file(self, index: int, name: str, rows_total: int | None = None) -> None:
        self.file_index = index
        self.file_name = name
        self.file_started = time.perf_counter()
        self.rows_total = rows_total
        self.rows_done = self.rows_error = self.rows_empty = self.rows_unparsed = 0
        self.cells_done = 0
        self.ai_candidates = self.ai_judged = self.ai_batches = self.ai_reused = 0
        self.ai_success = self.ai_failed = self.ai_skipped = 0
        self.review_pending = 0
        self.export_done = self.export_total = 0
        self.scope = {}
        self.last_rows = self.last_cells = 0
        self.last_emit_time = self.file_started
        self.stage("format")

    def stage(self, name: str) -> None:
        self.stage_name = name
        self.stage_started = time.perf_counter()
        self.emit(force=True)

    def row_progress(self, done: int, total: int | None = None, *,
                    errors: int = 0, empty: int = 0, unparsed: int = 0) -> None:
        """Report rows consumed by the streaming scanner."""
        self.rows_done = done
        if total is not None:
            self.rows_total = total
        self.rows_error = errors
        self.rows_empty = empty
        self.rows_unparsed = unparsed
        self.emit()

    def cell_progress(self, cells: int) -> None:
        self.cells_done = cells
        self.emit()

    def ai_progress(self, *, candidates: int | None = None, judged: int | None = None,
                    batches: int | None = None, reused: int | None = None,
                    success: int | None = None, failed: int | None = None,
                    skipped: int | None = None) -> None:
        """Record AI activity. Candidates and *actual inferences* are kept apart."""
        if candidates is not None:
            self.ai_candidates = candidates
        if judged is not None:
            self.ai_judged = judged
        if batches is not None:
            self.ai_batches = batches
        if reused is not None:
            self.ai_reused = reused
        if success is not None:
            self.ai_success = success
        if failed is not None:
            self.ai_failed = failed
        if skipped is not None:
            self.ai_skipped = skipped
        self.emit()

    def set_review_pending(self, count: int) -> None:
        self.review_pending = count
        self.emit()

    def export_progress(self, done: int, total: int) -> None:
        self.export_done = done
        self.export_total = total
        self.emit()

    def set_scope(self, **values) -> None:
        self.scope = dict(values)

    # --------------------------------------------------------------------- event
    def snapshot(self) -> ProgressEvent:
        now = time.perf_counter()
        window = now - self.last_emit_time
        overall = now - self.file_started
        # Throughput of the most recent reporting window; when the window is too
        # short to measure (for example a snapshot taken straight after an emit)
        # fall back to the run average rather than reporting nothing.
        rows_recent = (self.rows_done - self.last_rows) / window if window > 0.001 else None
        if rows_recent is None or rows_recent <= 0:
            rows_recent = self.rows_done / overall if overall > 0.001 else None
        cells_recent = (self.cells_done - self.last_cells) / window if window > 0.001 else None
        if cells_recent is None or cells_recent <= 0:
            cells_recent = self.cells_done / overall if overall > 0.001 else None
        eta = None
        # Only promise an ETA once there is enough throughput data to extrapolate.
        if self.rows_total and self.rows_done > 0 and rows_recent and rows_recent > 0:
            eta = max(0.0, (self.rows_total - self.rows_done) / rows_recent)
        return ProgressEvent(
            stage=self.stage_name,
            file_index=self.file_index,
            file_total=self.file_total,
            file_name=self.file_name,
            rows_total=self.rows_total,
            rows_done=self.rows_done,
            rows_error=self.rows_error,
            rows_empty=self.rows_empty,
            rows_unparsed=self.rows_unparsed,
            cells_done=self.cells_done,
            elapsed=overall,
            stage_elapsed=now - self.stage_started,
            rows_per_sec=self.rows_done / overall if overall > 0 else None,
            rows_per_sec_recent=rows_recent,
            cells_per_sec=cells_recent if self.cells_done else None,
            eta_seconds=eta,
            ai_candidates=self.ai_candidates,
            ai_judged=self.ai_judged,
            ai_batches=self.ai_batches,
            ai_reused=self.ai_reused,
            ai_success=self.ai_success,
            ai_failed=self.ai_failed,
            ai_skipped=self.ai_skipped,
            review_pending=self.review_pending,
            export_done=self.export_done,
            export_total=self.export_total,
            scope=self.scope,
        )

    def emit(self, force: bool = False) -> ProgressEvent | None:
        now = time.perf_counter()
        if not force and (now - self.last_emit) < self.interval:
            return None
        self.last_emit = now
        event = self.snapshot()
        self.last_rows, self.last_cells = self.rows_done, self.cells_done
        self.last_emit_time = now
        self.events.append(event)
        if self.sink is not None:
            try:
                self.sink(event)
            except Exception:  # a progress renderer must never break the analysis
                pass
        return event

    def finish(self) -> ProgressEvent:
        return self.emit(force=True)


def make_cli_tracker(stream=None, interval: float = 0.25, plain_interval: float = 10.0,
                     mode: str = "auto") -> ProgressTracker:
    """Build a tracker whose sink writes progress to the console.

    - interactive terminal: one line that refreshes in place (carriage return only,
      no ANSI escape sequences, so CMD and PowerShell both behave the same).
    - redirected/piped stdout: plain log lines at a coarse interval.
    """
    stream = stream or sys.stdout
    renderer = ConsoleProgress(stream, interval=interval, plain_interval=plain_interval, mode=mode)
    tracker = ProgressTracker(sink=renderer.render)
    tracker.renderer = renderer
    return tracker


class ConsoleProgress:
    """Render ProgressEvent for CMD/PowerShell/bash terminals."""

    def __init__(self, stream=None, interval: float = 0.25, plain_interval: float = 10.0,
                 mode: str = "auto", width: int = 100) -> None:
        self.stream = stream or sys.stdout
        self.interval = interval
        self.plain_interval = plain_interval
        self.mode = mode
        self.width = width
        self.interactive = mode == "auto" and getattr(self.stream, "isatty", lambda: False)()
        self.enabled = mode in ("auto", "plain")
        self.last_line = ""
        self.last_plain = 0.0
        self.last_stage = ""

    def render(self, event: ProgressEvent) -> None:
        if not self.enabled:
            return
        line = event.line()
        if self.interactive:
            # Pad to overwrite any previous longer line instead of using ANSI erase.
            padded = line.ljust(self.width)[: self.width]
            self.stream.write("\r" + padded)
            self.stream.flush()
            self.last_line = padded
        else:
            now = time.perf_counter()
            finished = event.rows_total and event.rows_done >= event.rows_total
            # A redirected stream cannot refresh, so print a line when the stage
            # changes, when the scan is done, or every `plain_interval` seconds.
            if (not self.last_line or event.stage != self.last_stage or finished
                    or now - self.last_plain >= self.plain_interval):
                self.last_plain = now
                self.last_stage = event.stage
                self.stream.write(line + "\n")
                self.stream.flush()
                self.last_line = line

    def finish(self) -> None:
        if self.interactive and self.last_line:
            self.stream.write("\n")
            self.stream.flush()
            self.last_line = ""
