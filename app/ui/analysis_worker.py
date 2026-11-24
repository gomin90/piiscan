"""Background analysis thread for the GUI.

Qt widgets may only be touched from the GUI thread, so the whole analysis runs in
a QThread and reports through signals. The window stays responsive while a
multi-million-cell CSV is scanned, and the user can cancel: the engine checks the
cancel flag between batches and returns the partial result with `cancelled=True`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from PySide6.QtCore import QObject, QThread, Signal

from app.ai import create_ai_engine
from app.core.analysis_service import analyse_file
from app.metadata_analysis.metadata_service import analyse_metadata_file
from app.ui.lifecycle import register_thread
from app.utils.logger import Diagnostics, get_logger


class MetadataWorker(QThread):
    """Function B: analyse DB metadata extracts off the GUI thread."""

    progress = Signal(dict)
    file_finished = Signal(dict)
    finished = Signal(list)
    failed = Signal(str)

    def __init__(self, files, config, hardware, mapping=None, diagnostics=None) -> None:
        super().__init__()
        self.files = list(files)
        self.config = config
        self.hardware = hardware
        self.mapping = mapping
        self.diagnostics = diagnostics or Diagnostics(enabled=config.debug)
        self.engine = None
        self.results: list[dict] = []
        self._cancel = False

    def request_cancel(self) -> None:
        self._cancel = True

    def start(self) -> None:  # noqa: N802 - Qt naming
        # Keep the thread joinable until it really finishes: destroying a QThread
        # while its thread still runs aborts the process.
        self.setObjectName("MetadataWorker")
        register_thread(self)
        super().start()

    def run(self) -> None:  # noqa: N802 - Qt naming
        try:
            self.engine = create_ai_engine(self.config, self.hardware)
            for index, path in enumerate(self.files, start=1):
                if self._cancel:
                    break
                self.progress.emit({"현재 분석 파일": Path(path).name,
                                    "파일 진행": f"{index}/{len(self.files)}"})
                result = analyse_metadata_file(
                    path, self.config, self.engine, mapping=self.mapping,
                    diagnostics=self.diagnostics,
                    should_stop=lambda: self._cancel,
                    progress=lambda done, total: self.progress.emit({
                        "처리 metadata Row": done, "전체 metadata Row": total,
                    }),
                )
                self.results.append(result)
                self.file_finished.emit(result)
        except Exception as exc:
            get_logger("gui").exception("메타정보 worker 실패")
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        finally:
            if self.engine is not None:
                try:
                    self.engine.close()
                except Exception:
                    pass
            self.finished.emit(self.results)


class AnalysisWorker(QThread):
    """Run `analyse_file` for each selected file off the GUI thread."""

    progress = Signal(dict)          # live counters for the progress panel
    file_finished = Signal(object)   # FileResult
    finished = Signal(list)          # list[FileResult]
    failed = Signal(str)

    def __init__(self, files: Iterable[Path], config, hardware, diagnostics: Diagnostics) -> None:
        super().__init__()
        self.files = list(files)
        self.config = config
        self.hardware = hardware
        self.diagnostics = diagnostics
        self.engine = None
        self.results: list = []
        self._cancel = False

    def request_cancel(self) -> None:
        self._cancel = True

    def cancelled(self) -> bool:
        return self._cancel

    def start(self) -> None:  # noqa: N802 - Qt naming
        self.setObjectName("AnalysisWorker")
        register_thread(self)
        super().start()

    def run(self) -> None:  # noqa: N802 - Qt naming
        try:
            self.engine = create_ai_engine(self.config, self.hardware)
            self.progress.emit({"AI 실행 상태": self.engine.summary().get("status", "-")})
            for index, path in enumerate(self.files, start=1):
                if index > 1 and self._cancel:
                    break
                self.progress.emit({
                    "현재 분석 파일": Path(path).name,
                    "파일 진행": f"{index}/{len(self.files)}",
                    "전체 파일 수": len(self.files),
                })
                result = analyse_file(
                    path, self.config, self.engine,
                    diagnostics=self.diagnostics,
                    should_stop=lambda: self._cancel,
                    progress=lambda done, total, cells=0, ai_calls=0: self.progress.emit({
                        "처리 행 수": done,
                        "전체 행 수": total,
                        "처리 Cell 수": f"{cells:,}",
                        "AI 추론 횟수": ai_calls,
                        "AI 진행률": f"{min(100, ai_calls * 100 // max(1, self.config.max_ai_cell_reviews))}%",
                    }),
                )
                self.results.append(result)
                self.file_finished.emit(result)
        except Exception as exc:  # a worker crash must not take the GUI down
            get_logger("gui").exception("분석 worker 실패")
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        finally:
            if self.engine is not None:
                try:
                    self.engine.close()
                except Exception:
                    pass
            self.finished.emit(self.results)
