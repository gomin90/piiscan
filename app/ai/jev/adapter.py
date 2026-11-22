"""Jev-Style Decision Model Adapter.

규칙 기반 엔진과 분리되어 있다. 모델/scorer이 없거나 실패하면 `load()`이
False를 return하고 규칙 기반 분석은 그대로 완료된다.

판정 구조는 Jev-Style 공식 decision protocol(State + Question + Options ->
선택지별 확률)을 그대로 사용한다. 공식 GGUF runtime(jev_style_decision_gguf.py)
이 모델 버전에 맞는 scorer(0.8B v3 -> jev-score, 2B v4 -> jev-score-v2)를
로컬 subprocess로 호출하며, localhost HTTP 서버나 외부 API는 사용 하지 않는다.
"""
from __future__ import annotations

import importlib.util
import sys
import time

from app.ai.gpu_detector import gpu_plan
from app.ai.jev.decision import (
    ADDRESS_OPTIONS,
    JEV_TYPE_TO_INTERNAL,
    NAME_CONTEXT_OPTIONS,
    PERSONALITY_OPTIONS,
    PROTECTION_OPTIONS,
    ROW_PERSONALITY_QUESTION,
    TYPE_OPTIONS,
    JevStyleDecisionRequest,
    JevStyleDecisionResult,
)
from app.ai.jev.model_manager import JevStyleModelSpec
from app.utils.masking import MASK_CHARS, mask_value

MAX_SAMPLES = 8
AI_RSS_LIMIT_MB = 3500.0  # 8GB RAM machine: stop AI before the process is at risk

PERSONALITY_QUESTION = "이 컬럼은 개인정보를 포함하거나 개인정보를 표현하는 컬럼인가?"
TYPE_QUESTION = "이 컬럼의 값은 어떤 개인정보 유형에 가장 해당하는가?"
NAME_QUESTION = "이 컬럼의 값은 실제 사람 이름인가, 아니면 다른 종류의 문자열인가?"
ADDRESS_QUESTION = "이 컬럼의 값은 주소의 어떤 부분인가?"
PROTECTION_QUESTION = "이 컬럼 데이터의 보호 상태는 무엇인가?"

TYPE_CATEGORY = "intent"
GENERAL_CATEGORY = "general"
PROTECTION_SUFFIXES = ("_MASK", "_MASKED", "_HASH", "_HASHED", "_ENC", "_ENCRYPT", "_CRYPT", "_SHA")
MARGIN_REVIEW = 0.05


HANGUL_RANGE = range(0xAC00, 0xD7A4)


def redact_sample(value: str) -> str:
    """AI 입력용 비가역 마스킹: 원문 개인정보를 그대로 전달하지 않는다."""
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    masked = mask_value(text)
    # 마스킹 후에도 남은 숫자 조각을 줄여 패턴만 남긴다
    if sum(ch.isdigit() for ch in masked) >= 4:
        masked = "".join(ch if not ch.isdigit() else "*" for ch in masked)
    return masked


def charset_profile(values: list[str]) -> dict:
    """값의 charset 분포만 aggregate로 전달 (원문 노출 없음)."""
    digits = hangul = latin = other = total = 0
    for value in values:
        for ch in str(value):
            total += 1
            if ch.isdigit():
                digits += 1
            elif ord(ch) in HANGUL_RANGE:
                hangul += 1
            elif ch.isalpha():
                latin += 1
            else:
                other += 1
    if not total:
        return {"digit": 0.0, "hangul": 0.0, "latin": 0.0, "other": 0.0}
    return {"digit": round(digits / total, 3), "hangul": round(hangul / total, 3),
            "latin": round(latin / total, 3), "other": round(other / total, 3)}


def shape_pattern(value: str) -> str:
    """값의 charset/length 구조만 남기는 비가역 패턴 (예: 999-9999, KKK, A*a***)."""
    out = []
    for ch in (value or "").strip():
        if ch.isdigit():
            out.append("9")
        elif ch in MASK_CHARS:
            out.append("*")
        elif ord(ch) in HANGUL_RANGE:
            out.append("K")
        elif ch.isalpha():
            out.append("A" if ch.isupper() else "a")
        else:
            out.append(ch)
    return "".join(out)


def pattern_counts(raw_samples: list[str], limit: int = 6) -> list[str]:
    """가장 흔한 값 형태 (예: 999-9999 x 4) -> 원문 없이 형태만."""
    counts: dict[str, int] = {}
    for sample in raw_samples:
        pattern = shape_pattern(sample)
        if pattern:
            counts[pattern] = counts.get(pattern, 0) + 1
    return [f"{pattern} x{count}" for pattern, count in sorted(counts.items(), key=lambda item: -item[1])[:limit]]


def structure_hints(patterns: list[str]) -> list[str]:
    """값 형태를 한국어 구조로 요약 (원문 없음). 예: "11자리 숫자", "하이픈 구분 숫자"."""
    hints: list[str] = []
    for pattern in patterns:
        body = pattern.split(" x")[0]
        digits = sum(ch.isdigit() for ch in body)
        hangul = sum(1 for ch in body if ord(ch) in HANGUL_RANGE)
        latin = sum(1 for ch in body if ch.isascii() and ch.isalpha())
        if "@" in body:
            hints.append("이메일 형식")
        elif digits == len(body) and digits >= 13:
            hints.append(f"{digits}자리 숫자")
        elif digits == len(body) and digits >= 10:
            hints.append(f"{digits}자리 숫자")
        elif digits and "-" in body:
            hints.append("하이픈 구분 숫자")
        elif hangul and digits == 0:
            hints.append(f"{hangul}글 한글 문자열")
        elif latin and digits == 0:
            hints.append(f"{latin}글 ascii 문자열")
        elif digits:
            hints.append("숫자+문자 혼합")
        else:
            hints.append("일반 문자열")
    return sorted(set(hints))[:6]


def build_column_state(column: dict, max_samples: int = MAX_SAMPLES) -> dict:
    """컬럼 -> Jev-Style State (통계 + 재마스킹된 패턴만; 원문 개인정보 없음)."""
    stats = column.get("stats", {}) or {}
    raw_samples = [str(sample) for sample in (stats.get("samples") or [])[:max_samples]]
    samples = [redact_sample(sample) for sample in raw_samples]
    patterns = []
    for sample in raw_samples:
        pattern = shape_pattern(sample)
        if pattern and pattern not in patterns:
            patterns.append(pattern)
    rows = max(stats.get("rows", 0), 1)
    return {
        "schema": column.get("schema", "") or "-",
        "table": column.get("table", "") or "-",
        "column": column.get("column", ""),
        "column_type": column.get("column_type", "TEXT"),
        "sample_count": len(samples),
        "null_ratio": round(stats.get("null", 0) / rows, 3),
        "unique_ratio": round(stats.get("distinct_estimate", 0) / max(stats.get("non_empty", 1), 1), 3),
        "masked_like": stats.get("masked_like", 0),
        "numeric_ratio": round(stats.get("numeric_ratio", 0), 3),
        "length": {
            "min": stats.get("min_len", 0),
            "max": stats.get("max_len", 0),
            "avg": round(stats.get("avg_len", 0), 1),
        },
        "charset": charset_profile(raw_samples),
        "value_patterns": pattern_counts(raw_samples),
        "lengths": sorted({len(str(sample)) for sample in raw_samples if sample}),
        "masked_samples": samples,
        "related_columns": [str(name)[:40] for name in column.get("related_columns", [])][:6],
    }


class JevStyleAdapter:
    def __init__(self, spec: JevStyleModelSpec | None, hardware=None, threads: int = 2,
                 rss_limit_mb: float = AI_RSS_LIMIT_MB, max_len: int | None = None,
                 gpu_layers: int | None = None) -> None:
        self.spec = spec
        self.hardware = hardware
        self.threads = max(1, threads)
        self.max_len = max_len
        self.gpu_layers = gpu_layers
        self.rss_limit_mb = rss_limit_mb
        self.engine = None
        self.module = None
        self.status = "Jev-Style 모델 없음"
        self.backend = "none"
        self.load_seconds = 0.0
        self.inference_seconds = 0.0
        self.decisions = 0
        self.failures = 0
        self.row_reviews = 0
        self.peak_rss_mb = 0.0
        self.last_error: str | None = None
        self.scorer_info: dict = {}
        self.gpu_report: dict = {}

    # -------------------------------------------------------------- lifecycle
    def load(self) -> bool:
        if self.spec is None:
            self.status = "Jev-Style 모델 없음 -> 규칙 기반 분석만 사용"
            return False
        if not self.spec.verified:
            self.status = "모델 무결성 검증 실패 -> AI 판정 건너뛰기"
            self.last_error = "; ".join(self.spec.problems)
            return False
        if self.spec.scorer_path is None:
            self.status = "scorer 바이너리 미빌드 -> 규칙 결과만 사용"
            self.last_error = "; ".join(self.spec.problems)
            return False

        # A GPU only helps if the matching ggml backend library sits next to the scorer.
        backend, gpu_layers, gpu_note = gpu_plan(
            self.spec.scorer_path, self.hardware, self.gpu_layers, self.spec.size_bytes
        )
        if gpu_note:
            self.last_error = gpu_note

        model_gb = max(self.spec.size_bytes / (1024 ** 3), self.spec.rss_estimate_mb / 1024.0)
        available = getattr(self.hardware, "available_ram_gb", 999.0)
        # With a GPU backend the weights live in VRAM, so system RAM only needs headroom.
        ram_needed = 0.8 if gpu_layers > 0 else model_gb + 0.5
        if available < ram_needed:
            self.status = (
                f"RAM 부족 (필요 ~{ram_needed:.1f}GB, 가능 {available:.1f}GB) -> 규칙 결과만 사용"
            )
            self.last_error = "insufficient RAM for the Jev-Style model"
            return False

        started = time.perf_counter()
        module_name = f"jev_style_runtime_{self.spec.key}"
        try:
            module_spec = importlib.util.spec_from_file_location(module_name, self.spec.runtime_path)
            module = importlib.util.module_from_spec(module_spec)
            sys.modules[module_name] = module
            module_spec.loader.exec_module(module)
            self.module = module
            engine = module.JevStyleDecisionGGUF(
                model_dir=str(self.spec.directory),
                quant=self.spec.quant,
                gguf=str(self.spec.gguf_path),
                binary=str(self.spec.scorer_path),
                n_gpu_layers=gpu_layers,
                threads=self.threads,
                **({"max_len": self.max_len} if self.max_len else {}),
            )
            self.engine = engine
            self.scorer_info = dict(getattr(engine, "info", {}) or {})
            # The scorer reports what it actually managed to offload; trust that over the plan.
            if self.scorer_info.get("gpu_offload") is False and backend != "cpu":
                backend = "cpu"
                gpu_layers = 0
                gpu_note = (
                    f"scorer binary was built without GPU support ({self.spec.scorer_path}); "
                    "rebuild with GGML_CUDA/-metal to use the GPU"
                )
            gpu_report = self.scorer_info.get("gpu") or {}
            self.gpu_report = gpu_report
            layers_on_gpu = gpu_report.get("layers_on_gpu", "cpu")
            if backend != "cpu":
                if not gpu_report.get("gpu_backend_present"):
                    backend = "cpu"
                    gpu_layers = 0
                    gpu_note = (
                        "no GPU backend could be loaded by llama.cpp "
                        + (gpu_report.get("backend_log") or "(no backend log)")
                        + " -> CPU inference"
                    )
                elif layers_on_gpu == "cpu" or layers_on_gpu.startswith("0/"):
                    backend = "cpu"
                    gpu_layers = 0
                    gpu_note = (
                        f"GPU backend present but 0 layers offloaded (requested ngl {gpu_report.get('requested_ngl')}) "
                        + (": " + (gpu_report.get("backend_log") or "").strip() if gpu_report.get("backend_log") else "")
                        + " -> CPU inference"
                    )
                else:
                    gpu_note = f"layers on GPU: {layers_on_gpu}"
        except Exception as exc:  # 모델 로드/scorer 초기화 실패 -> 규칙 기반 유지
            self.status = f"Jev-Style 모델 로드 실패 -> 규칙 결과만 사용: {exc}"
            self.last_error = str(exc)
            self.engine = None
            return False

        self.backend = backend
        self.gpu_layers = gpu_layers
        self.gpu_report = self.scorer_info.get("gpu") or {}
        self.status = f"로드 완료 ({self.spec.model_name}, {self.spec.quant}, {backend} backend)"
        if gpu_note:
            self.status += f" ({gpu_note})"
        self.load_seconds = time.perf_counter() - started
        self._sample_rss()
        return True

    def _sample_rss(self) -> float:
        try:
            import psutil
        except ImportError:
            return 0.0
        rss = psutil.Process().memory_info().rss / (1024 ** 2)
        process = getattr(self.engine, "proc", None)
        if process is not None and process.poll() is None:
            try:
                rss += psutil.Process(process.pid).memory_info().rss / (1024 ** 2)
            except Exception:
                pass
        self.peak_rss_mb = max(self.peak_rss_mb, rss)
        return rss

    def memory_usage(self) -> dict:
        return {"rss_mb": round(self._sample_rss(), 1), "peak_rss_mb": round(self.peak_rss_mb, 1)}

    def over_memory_limit(self) -> bool:
        return self._sample_rss() > self.rss_limit_mb

    def close(self) -> None:
        if self.engine is not None:
            try:
                self.engine.close()
            except Exception:
                pass
            self.engine = None
            self.status = "AI 자원 해제 완료"
        self.backend = "none"

    # -------------------------------------------------------------- decisions
    def decide(self, request: JevStyleDecisionRequest) -> JevStyleDecisionResult:
        if self.engine is None:
            return JevStyleDecisionResult(scenario=request.scenario, decision=None, error="AI 미로드")
        started = time.perf_counter()
        try:
            raw = self.engine.decide(request.state, request.question, options=request.options,
                                    qtype=request.qtype, category=request.category)
        except Exception as exc:
            self.failures += 1
            self.inference_seconds += time.perf_counter() - started
            return JevStyleDecisionResult(scenario=request.scenario, decision=None,
                                         error=f"{type(exc).__name__}: {exc}",
                                         model=self.spec.model_name if self.spec else "")
        elapsed = time.perf_counter() - started
        self.inference_seconds += elapsed
        self.decisions += 1
        result = self._to_result(request.scenario, raw, elapsed)
        self._sample_rss()
        return result

    def decide_many(self, requests: list[JevStyleDecisionRequest]) -> list[JevStyleDecisionResult]:
        """Same state, several questions -> one shared jev-score request."""
        if self.engine is None or not requests:
            return [JevStyleDecisionResult(request.scenario, None, error="AI 미로드") for request in requests]
        started = time.perf_counter()
        results: list[JevStyleDecisionResult] = []
        shared_state = requests[0].state
        if all(request.state == shared_state for request in requests):
            questions = [{"t": request.qtype, "ins": request.question, "crit": request.options}
                         for request in requests]
            try:
                raws = self.engine.decide_many(shared_state, questions, category=requests[0].category)
            except Exception as exc:
                self.failures += len(requests)
                self.inference_seconds += time.perf_counter() - started
                return [JevStyleDecisionResult(request.scenario, None, error=f"{type(exc).__name__}: {exc}")
                        for request in requests]
            elapsed = time.perf_counter() - started
            self.inference_seconds += elapsed
            self.decisions += len(requests)
            results = [self._to_result(request.scenario, raw, elapsed / len(requests))
                       for request, raw in zip(requests, raws)]
        else:
            # Different states cannot share one decode; fall back to per-request decide().
            for request in requests:
                results.append(self.decide(request))
        self._sample_rss()
        return results

    def _to_result(self, scenario: str, raw: dict, elapsed: float) -> JevStyleDecisionResult:
        probabilities = {str(key): float(value) for key, value in raw.get("probabilities", {}).items()}
        ranked = sorted(probabilities.values(), reverse=True)
        return JevStyleDecisionResult(
            scenario=scenario,
            decision=raw.get("answer"),
            probabilities=probabilities,
            confidence=float(raw.get("top_probability", ranked[0] if ranked else 0.0)),
            margin=ranked[0] - (ranked[1] if len(ranked) > 1 else 0.0),
            input_tokens=int(raw.get("input_tokens", 0)),
            latency_ms=elapsed * 1000.0,
            model=raw.get("model", self.spec.model_name if self.spec else ""),
            backend=raw.get("backend", self.backend),
            raw={key: value for key, value in raw.items() if key != "scores"},
        )

    # -------------------------------------------------------------- scenarios
    def classify_column(self, column: dict) -> dict:
        """개인정보 여부 + 유형 + (필요 시) 이름/주소/보호 의미 판정."""
        state = build_column_state(column)
        name = (column.get("column") or "").upper()
        requests = [
            JevStyleDecisionRequest("personality", state, PERSONALITY_QUESTION, PERSONALITY_OPTIONS,
                                    "choice", GENERAL_CATEGORY),
            JevStyleDecisionRequest("type", state, TYPE_QUESTION, TYPE_OPTIONS, "choice", TYPE_CATEGORY),
        ]
        if "NAME" in name or "NM" in name or column.get("hangul_ratio", 0) > 0.3:
            requests.append(JevStyleDecisionRequest("name_context", state, NAME_QUESTION,
                                                   NAME_CONTEXT_OPTIONS, "choice", GENERAL_CATEGORY))
        if "ADDR" in name or column.get("address_like", False):
            requests.append(JevStyleDecisionRequest("address", state, ADDRESS_QUESTION,
                                                   ADDRESS_OPTIONS, "choice", GENERAL_CATEGORY))
        if any(suffix in name for suffix in PROTECTION_SUFFIXES):
            requests.append(JevStyleDecisionRequest("protection", state, PROTECTION_QUESTION,
                                                   PROTECTION_OPTIONS, "choice", GENERAL_CATEGORY))

        results = self.decide_many(requests)
        by_scenario = {result.scenario: result for result in results}
        personality = by_scenario.get("personality")
        type_result = by_scenario.get("type")
        internal = JEV_TYPE_TO_INTERNAL.get(type_result.decision or "", "UNCERTAIN")

        reasons = []
        if personality and personality.ok:
            reasons.append(f"개인정보 여부 {personality.decision} (p={personality.confidence:.2f})")
        if type_result and type_result.ok:
            reasons.append(f"유형 {type_result.decision} (p={type_result.confidence:.2f})")
        low_margin = bool(type_result and type_result.margin < MARGIN_REVIEW)
        if low_margin and type_result.decision not in (None, "UNCERTAIN"):
            reasons.append("선택지 확률 차이가 작음 -> 검토 필요")

        return {
            "column": column.get("column", ""),
            "privacy_type": internal,
            "jev_type": type_result.decision if type_result else None,
            "confidence": round(type_result.confidence, 3) if type_result else 0.0,
            "personality": personality.decision if personality else None,
            "reason": "; ".join(reasons) or "AI 판정 근거 없음",
            "requires_review": bool(type_result and (type_result.uncertain or low_margin)),
            "low_margin": low_margin,
            "decisions": {result.scenario: result.as_dict() for result in results},
            "samples_used": state["masked_samples"],
        }

    def classify_row(self, record: dict) -> dict:
        """Row-context check for ambiguous rows.

        Only masked cell patterns and column names reach the model - no raw value.
        This is a bounded call: it is used for rows the rule engine could not
        settle, never for every cell.
        """
        cells = record.get("cells", []) or []
        samples = [str(cell.get("value", "")) for cell in cells][:MAX_SAMPLES]
        state = build_column_state({
            "column": f"ROW {record.get('row', '')}",
            "schema": record.get("schema", ""),
            "table": record.get("table", ""),
            "stats": {
                "rows": 1,
                "non_empty": len(cells),
                "null": 0,
                "masked_like": sum(1 for cell in cells if cell.get("masked")),
                "numeric_ratio": round(sum(cell.get("value", "").isdigit() for cell in cells) / max(len(cells), 1), 3),
                "min_len": min((len(s) for s in samples), default=0),
                "max_len": max((len(s) for s in samples), default=0),
                "avg_len": round(sum(len(s) for s in samples) / max(len(samples), 1), 1),
                "samples": samples,
            },
            "related_columns": [str(cell.get("column", "")) for cell in cells],
        })
        request = JevStyleDecisionRequest("row_context", state, ROW_PERSONALITY_QUESTION,
                                         PERSONALITY_OPTIONS, "choice", GENERAL_CATEGORY)
        result = self.decide_many([request])[0]
        self.row_reviews += 1
        if not result.ok:
            return {"row": record.get("row"), "personality": None, "confidence": 0.0,
                    "reason": result.error or "AI 판정 없음", "requires_review": True}
        return {
            "row": record.get("row"),
            "personality": result.decision,
            "confidence": round(result.confidence, 3),
            "reason": f"Row 문맥 {result.decision} (p={result.confidence:.2f}, margin={result.margin:.2f})",
            "requires_review": result.uncertain or result.margin < MARGIN_REVIEW,
            "latency_ms": round(result.latency_ms, 1),
        }

    def summary(self) -> dict:
        return {
            "model": self.spec.model_name if self.spec else None,
            "model_key": self.spec.key if self.spec else None,
            "quant": self.spec.quant if self.spec else None,
            "scorer": str(self.spec.scorer_path) if self.spec and self.spec.scorer_path else None,
            "status": self.status,
            "backend": self.backend,
            "gpu_layers": getattr(self, "gpu_layers", 0),
            "gpu_backend": getattr(self.hardware, "gpu_backend_ready", False),
            "layers_on_gpu": (getattr(self, "gpu_report", {}) or {}).get("layers_on_gpu", "cpu"),
            "gpu_devices": (getattr(self, "gpu_report", {}) or {}).get("gpu_devices", []),
            "load_seconds": round(self.load_seconds, 2),
            "inference_seconds": round(self.inference_seconds, 3),
            "decisions": self.decisions,
            "row_reviews": self.row_reviews,
            "failures": self.failures,
            "peak_rss_mb": round(self.peak_rss_mb, 1),
            "context_limit": self.spec.context_limit if self.spec else 0,
            "scorer_info": self.scorer_info,
            "error": self.last_error,
        }
