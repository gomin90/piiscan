"""Privacy-safe logging. 원문 CSV 값이나 개인정보는 절대 기록하지 않는다."""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

DEBUG = os.environ.get("PIISCAN_DEBUG", "").lower() in ("1", "true", "yes")

_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")


def get_logger(name: str = "piiscan") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(_formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG if DEBUG else logging.INFO)
    return logger


class Diagnostics:
    """Build-time debug 모드에서 수집하는 진단 데이터 (원문값 포함 금지)."""

    def __init__(self, enabled: bool = DEBUG):
        self.enabled = enabled
        self.events: list[dict] = []
        self.started = time.time()

    def record(self, event: str, **data) -> None:
        if not self.enabled:
            return
        entry = {"event": event, "ts": round(time.time() - self.started, 3)}
        entry.update(data)
        self.events.append(entry)
        get_logger("diagnostics").debug(event + ": " + ", ".join(f"{k}={v}" for k, v in data.items()))

    def dump(self, path: Path | None = None) -> str:
        import json

        text = json.dumps({"events": self.events}, ensure_ascii=False, indent=2)
        if path:
            path.write_text(text, encoding="utf-8")
        return text
