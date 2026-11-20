"""130,000행 이상 파일의 스트리밍 분석 성능 / 메모리 검증."""
from __future__ import annotations

import os
import time

from app.config.settings import AnalysisConfig
from app.core.analysis_service import analyse_file
from tests.fixtures.generate_fixtures import generate_large


def test_130k_rows_streaming():
    path = generate_large(130000)
    # AI 모델/tokenizer RSS는 test_ai_memory_bounded에서 separately 측정한다.
    config = AnalysisConfig(batch_size=1000, use_ai=False)
    try:
        import psutil
        base_mb = psutil.Process().memory_info().rss / (1024 ** 2)
    except ImportError:
        base_mb = 0.0
    started = time.perf_counter()
    result = analyse_file(path, config)
    elapsed = time.perf_counter() - started
    assert result.rows_parsed == 130000
    assert result.settings["mode"] == "전수"

    # Streaming must keep this process's RSS bounded: 130k rows x 11 columns of
    # short strings loaded as a DataFrame would be far larger than the cap below.
    try:
        import psutil

        rss_mb = psutil.Process().memory_info().rss / (1024 ** 2)
        growth = rss_mb - base_mb
        assert growth < 120.0, f"streaming engine exceeded memory budget: +{growth:.1f}MB"
    except ImportError:
        pass

    assert elapsed < 120
    assert os.path.getsize(path) > 5_000_000
