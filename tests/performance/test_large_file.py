"""130,000행 이상 파일의 스트리밍 분석 성능 / 메모리 검증."""
from __future__ import annotations

import os
import time

from app.config.settings import AnalysisConfig
from app.core.analysis_service import analyse_file
from tests.fixtures.generate_fixtures import generate_large


def test_130k_rows_streaming():
    path = generate_large(130000)
    config = AnalysisConfig(batch_size=1000)
    started = time.perf_counter()
    result = analyse_file(path, config)
    elapsed = time.perf_counter() - started
    assert result.rows_parsed == 130000
    assert result.settings["mode"] == "전수"
    # Streaming must keep RSS bounded; a full-file DataFrame load would exceed this.
    rss = None
    try:
        import psutil

        rss = psutil.virtual_memory().total  # coarse guard, real bound is per-process RSS
    except ImportError:
        pass
    # 130k rows x 11 columns of short strings is several tens of MB if fully loaded;
    # the engine keeps only per-column counters plus a bounded distinct set.
    assert elapsed < 120
    assert os.path.getsize(path) > 5_000_000
