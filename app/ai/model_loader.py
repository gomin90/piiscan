"""GGUF 모델 Locate + SHA256 무결성 검증.

폐쇄망 배포에서는 모델 다운로드가 금지되므로 models/ 디렉터리에 포함된 GGUF 파일만
사용하며, manifest의 SHA256과 일치하는지 확인한다.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from app.config.settings import project_root

MANIFEST_NAME = "model_manifest.json"
PREFERRED_ORDER = (1.5, 1.0, 0.5)  # 8GB RAM 우선: 작지만 의미적으로 충분한 모델 순


@dataclass
class ModelInfo:
    path: str
    name: str
    size_bytes: int
    sha256: str
    verified: bool
    parameter_billions: float | None


def _size_billion_guess(name: str) -> float | None:
    for token in name.lower().replace("_", ".").split("."):
        if token.endswith("b") and token[:-1].isdigit():
            return float(token[:-1])
    return None


def discover_models(models_dir: Path | None = None) -> list[ModelInfo]:
    models_dir = models_dir or project_root() / "models"
    manifest = {}
    manifest_path = models_dir / MANIFEST_NAME
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            manifest = {}
        entries = {entry.get("file"): entry for entry in manifest.get("models", [])}
    else:
        entries = {}

    models: list[ModelInfo] = []
    if not models_dir.exists():
        return models
    for path in sorted(models_dir.glob("*.gguf")):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        expected = entries.get(path.name, {}).get("sha256")
        models.append(
            ModelInfo(
                path=str(path),
                name=path.name,
                size_bytes=path.stat().st_size,
                sha256=digest,
                verified=bool(expected) and expected == digest,
                parameter_billions=_size_billion_guess(path.name),
            )
        )
    models.sort(key=lambda m: (m.parameter_billions is None, m.parameter_billions or 99))
    return models


def select_model(available_ram_gb: float, models_dir: Path | None = None) -> ModelInfo | None:
    for model in discover_models(models_dir):
        if not model.verified:
            continue
        if model.size_bytes / (1024 ** 3) + 1.2 <= available_ram_gb:
            return model
    return None
