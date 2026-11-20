"""Jev-Style 모델/scorer 발견 + 무결성 검증.

폐쇄망 배포에서 모델은 로컬 파일에서만 로드 된다.
`models/jev-style/<release>/` 구조의 GGUF, tokenizer, readout_config,
scorer 바이너리를 repo manifest의 SHA256과 비교하여 확인한다.
"""
from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path

from app.config.settings import project_root

REGISTRY = {
    "0.8b-v3": {
        "repo": "chaoliangUNSW/Jev-Style-0.8B-Decision-v3-GGUF",
        "model_name": "Jev-Style-0.8B-Decision-v3",
        "quant": "Q4_K_M",
        "gguf": "Jev-Style-0.8B-Decision-v3-Q4_K_M.gguf",
        "scorer_src": "jev_score.cpp",
        "scorer_bin": "jev-score",
        "runtime": "jev_style_decision_gguf.py",
        "context_limit": 25600,
        "license": "Apache-2.0",
        # 실제 macOS 실측(tools/jev_hardware.py): Metal 1.4GB / CPU 2.3GB peak RSS
        "rss_estimate_mb": 2300,
    },
    "2b-v4": {
        "repo": "chaoliangUNSW/Jev-Style-2B-Decision-v4-GGUF",
        "model_name": "Jev-Style-2B-Decision-v4",
        "quant": "Q4_K_M",
        "gguf": "Jev-Style-2B-Decision-v4-Q4_K_M.gguf",
        "scorer_src": "jev_score_v2.cpp",
        "scorer_bin": "jev-score-v2",
        "runtime": "jev_style_decision_gguf.py",
        "context_limit": 32768,
        "license": "Apache-2.0",
        # 실제 macOS 실측: Metal 2.3GB / CPU 3.7GB peak RSS
        "rss_estimate_mb": 3700,
    },
}


@dataclass
class JevStyleModelSpec:
    key: str
    repo: str
    model_name: str
    quant: str
    license: str
    context_limit: int
    directory: Path
    gguf_path: Path
    scorer_path: Path | None
    runtime_path: Path
    size_bytes: int
    sha256: str
    expected_sha256: str | None
    verified: bool
    rss_estimate_mb: int = 0
    architecture: dict = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "repo": self.repo,
            "model": self.model_name,
            "quant": self.quant,
            "license": self.license,
            "context_limit": self.context_limit,
            "rss_estimate_mb": self.rss_estimate_mb,
            "gguf": str(self.gguf_path),
            "scorer": str(self.scorer_path) if self.scorer_path else None,
            "size_bytes": self.size_bytes,
            "size_gb": round(self.size_bytes / (1024 ** 3), 3),
            "sha256": self.sha256,
            "expected_sha256": self.expected_sha256,
            "verified": self.verified,
            "architecture": self.architecture,
            "problems": self.problems,
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_gguf_metadata(path: Path, keys: tuple[str, ...]) -> dict:
    """Read scalar/string GGUF header keys without any dependency."""
    scalar = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}
    wanted, out = set(keys), {}
    with path.open("rb") as handle:
        def read(fmt: str):
            return struct.unpack(fmt, handle.read(struct.calcsize(fmt)))[0]

        # explicit, simple header walk
        if handle.read(4) != b"GGUF":
            return {}
        read("<I")          # version
        read("<Q")          # n_tensors
        for _ in range(read("<Q")):
            key_len = read("<Q")
            key = handle.read(key_len).decode("utf-8", "replace")
            vtype = read("<I")
            if vtype in scalar:
                value = read(scalar[vtype])
            elif vtype == 8:
                value = handle.read(read("<Q")).decode("utf-8", "replace")
            elif vtype == 9:
                elem, count = read("<I"), read("<Q")
                value = None
                for _ in range(count):
                    if elem in scalar:
                        read(scalar[elem])
                    elif elem == 8:
                        handle.read(read("<Q"))
                    else:
                        return {}
            else:
                return {}
            if key in wanted:
                out[key] = value
                if len(out) == len(wanted):
                    break
    return out


class JevStyleModelManager:
    """Find and integrity-check the local Jev-Style decision models."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or project_root() / "models" / "jev-style"

    def discover(self) -> list[JevStyleModelSpec]:
        specs: list[JevStyleModelSpec] = []
        for key, meta in REGISTRY.items():
            directory = self.root / key
            gguf = directory / meta["gguf"]
            if not gguf.is_file():
                continue
            manifest_path = directory / "manifest.json"
            expected = None
            if manifest_path.is_file():
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    expected = manifest.get("files", {}).get(meta["gguf"], {}).get("sha256")
                except (json.JSONDecodeError, OSError):
                    expected = None

            candidate_scorers = [directory / "build" / meta["scorer_bin"]]
            env_bin = Path(__import__("os").environ.get("JEV_SCORE_BIN", "")) if meta["scorer_bin"] == "jev-score" else Path(
                __import__("os").environ.get("JEV_SCORE_V2_BIN", ""))
            if env_bin.is_file():
                candidate_scorers.append(env_bin)
            scorer = next((path for path in candidate_scorers if path.is_file()), None)

            architecture = read_gguf_metadata(gguf, ("general.architecture", "general.name", "general.quantization_version"))
            problems: list[str] = []
            digest = _sha256(gguf)
            if expected and digest != expected:
                problems.append(f"SHA256 mismatch (manifest expects {expected[:16]}..., got {digest[:16]}...)")
            runtime_path = directory / meta["runtime"]
            if not runtime_path.is_file():
                problems.append("Jev-Style GGUF runtime (jev_style_decision_gguf.py) missing")
            tokenizer = directory / "tokenizer" / "tokenizer.json"
            if not tokenizer.is_file():
                problems.append("tokenizer/tokenizer.json missing")
            if not (directory / "readout_config.json").is_file():
                problems.append("readout_config.json missing (calibrated temperatures unavailable)")
            if scorer is None:
                problems.append(f"scorer binary '{meta['scorer_bin']}' not built (run build_jev_score.sh)")

            specs.append(
                JevStyleModelSpec(
                    key=key,
                    repo=meta["repo"],
                    model_name=meta["model_name"],
                    quant=meta["quant"],
                    license=meta["license"],
                    context_limit=meta["context_limit"],
                    rss_estimate_mb=meta.get("rss_estimate_mb", 0),
                    directory=directory,
                    gguf_path=gguf,
                    scorer_path=scorer,
                    runtime_path=runtime_path,
                    size_bytes=gguf.stat().st_size,
                    sha256=digest,
                    expected_sha256=expected,
                    verified=bool(expected) and digest == expected and not problems,
                    architecture=architecture,
                    problems=problems,
                )
            )
        return specs

    def select(self, available_ram_gb: float | None = None, prefer: str | None = None) -> JevStyleModelSpec | None:
        specs = [spec for spec in self.discover() if spec.verified and spec.scorer_path]
        if prefer:
            for spec in specs:
                if spec.key == prefer:
                    return spec
        # smaller model first so an 8GB machine can still run the AI layer
        specs.sort(key=lambda spec: spec.size_bytes)
        if available_ram_gb is not None:
            specs = [spec for spec in specs
                     if (spec.rss_estimate_mb / 1024.0 + 0.5) <= available_ram_gb]
        return specs[0] if specs else None
