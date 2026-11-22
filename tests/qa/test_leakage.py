"""QA: 원문 개인정보 유출 검증 (로그 / CSV / TXT / LLM 프롬프트 / 예외 경로).

Every fixture value carries a canary substring so a leak is detectable by a
literal string search of every output the tool produces.
"""
from __future__ import annotations

import io
import json
import logging
import re
import sys
from pathlib import Path

import pytest

from app.core.analysis_service import analyse_file
from app.export.csv_exporter import write_csv
from app.export.hit_exporter import write_hit_rows, write_row_records
from app.export.txt_exporter import write_txt
from app.utils.logger import Diagnostics

CANARY = "LEAKCANARY"

RAW_VALUES = [
    "김민준9001011234567",          # concatenated raw name+resident number
    "900101-1234567",              # resident number w/ birth/date part
    "010-9876-5432",               # phone
    "canaryuser@leakcorp.example",  # email
    "4111 1111 1111 1111",         # card PAN
    "100001234567",                # bank account
    "Seoul Canary Street 42",      # address
    "1990-01-01",                  # birth date
    "37.566520",                   # coordinate
    "450123456789012",             # IMSI
]


def build_fixture(path: Path) -> None:
    header = ["NAME_RAW", "RESIDENT_RAW", "PHONE_RAW", "EMAIL_RAW", "CARD_RAW",
              "BANK_RAW", "ADDR_RAW", "BIRTH_RAW", "COORD_RAW", "IMSI_RAW"]
    rows = [
        ["김민준9001011234567", "9001011234567", "010-9876-5432",
         "canaryuser@leakcorp.example", "4111 1111 1111 1111",
         "100001234567", "Seoul Canary Street 42", "1990-01-01",
         "37.566520", "450123456789012"],
        ["박길동9001022345678", "900102-234567-8", "010-1111-2222",
         "two@leakcorp.example", "5500000000000004", "1234-567890-1234",
         "Busan Canary Avenue 7", "19851113", "-122.419416", "310000000000007"],
        # Third row is deliberately ambiguous: only review-level evidence, so the
        # row-context AI path is exercised.
        ["김**", "199001-******-1", "010-***-5432", "canary*@leakcorp.example",
         "4111-****-****-1111", "10000***4567", "Seoul Canary St 4*", "1990-01-*",
         "37.56*", "450123*******12"],
    ]
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(",".join(header) + "\n")
        for row in rows:
            handle.write(",".join(row) + "\n")


def scan(text: str) -> list[str]:
    """Return raw fixture values found in `text`."""
    hits = []
    for value in RAW_VALUES:
        if value in text:
            hits.append(value)
    # Also catch the leak-contiguity patterns the masking helpers are meant to break.
    for pattern in (r"010-\d{4}-\d{4}", r"[a-z0-9]+@leakcorp\.example",
                    r"\d{13}", r"4111\s?1111\s?1111\s?1111", r"Canary Street 42"):
        if re.search(pattern, text):
            hits.append(pattern)
    return sorted(set(hits))


@pytest.fixture()
def fixture_path(tmp_path: Path) -> Path:
    path = tmp_path / "leak_canary.csv"
    build_fixture(path)
    return path


def capture_logs():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    root = logging.getLogger()
    root.addHandler(handler)
    previous = root.level
    root.setLevel(logging.DEBUG)
    return stream, handler, root, previous


def analyse_with_prompts(path: Path, prompts: list[str]) -> object:
    """Run the pipeline with a fake LLM that records every prompt it receives."""
    from app.ai.llm_manager import LocalLLMEngine

    engine = LocalLLMEngine()
    engine.llm = lambda *args, **kwargs: ""
    engine.backend = "stub"

    def fake_generate(prompt: str) -> str:
        prompts.append(prompt)
        return json.dumps({"column": "X", "privacy_type": "NONE", "confidence": 0.1,
                           "reason": "stub", "requires_review": True})

    engine._generate = fake_generate
    # Pretend a model is loaded so judge_column goes through the prompt path.
    from app.ai.model_loader import ModelInfo
    from app.ai.cpu_fallback import InferencePlan

    engine.model = ModelInfo("stub.gguf", "stub.gguf", 1_000_000, "x", True, 1.5)
    engine.plan = InferencePlan("cpu", 0, 2, 2048, True, "stub model for prompt capture")
    engine.status = "stub loaded"
    engine.load = lambda: True
    engine.memory_usage = lambda: {"rss_mb": 120.0}
    return analyse_file(path, llm=engine)


def test_logs_contain_no_raw_values(fixture_path: Path):
    stream, handler, root, previous = capture_logs()
    try:
        analyse_file(fixture_path)
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)
    hits = scan(stream.getvalue())
    assert not hits, f"raw personal data written to logs: {hits}"


def test_exports_contain_no_raw_values(fixture_path: Path, tmp_path: Path):
    result = analyse_file(fixture_path)
    csv_path = write_csv([r.as_dict() for r in result.results], tmp_path / "out.csv")
    txt_path = write_txt([result], tmp_path / "out.txt")
    hits_path = write_hit_rows([result], tmp_path / "hits.csv")
    rows_path = write_row_records([result], tmp_path / "rows.csv")
    text = (Path(csv_path).read_text(encoding="utf-8-sig")
            + Path(txt_path).read_text(encoding="utf-8")
            + Path(hits_path).read_text(encoding="utf-8-sig")
            + Path(rows_path).read_text(encoding="utf-8-sig"))
    hits = scan(text)
    assert not hits, f"raw personal data in exports: {hits}"
    assert "개인정보" in text
    # the row-level hit file must still carry evidence, just masked
    assert "행" in Path(hits_path).read_text(encoding="utf-8-sig")
    row_text = Path(rows_path).read_text(encoding="utf-8-sig")
    assert "행번호" in row_text and "개인정보 존재" in row_text


def test_debug_diagnostics_contain_no_raw_values(fixture_path: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PIISCAN_DEBUG", "1")
    diagnostics = Diagnostics(enabled=True)
    analyse_file(fixture_path, diagnostics=diagnostics)
    dump = diagnostics.dump(tmp_path / "diagnostics.json")
    hits = scan(dump)
    assert not hits, f"raw personal data in diagnostics: {hits}"


def test_llm_prompts_contain_no_raw_values(fixture_path: Path):
    prompts: list[str] = []
    analyse_with_prompts(fixture_path, prompts)
    assert prompts, "no LLM prompt was captured"
    row_prompts = [p for p in prompts if '"rule_basis"' in p]
    assert row_prompts, "no row-context prompt was captured (Row 문맥 판정 경로)"
    for prompt in prompts:
        hits = scan(prompt)
        assert not hits, f"raw personal data sent to LLM prompt: {hits}\n{prompt[:400]}"
        # Every sample handed to the model must be redacted.
        if '"masked_samples"' in prompt:
            sample_block = prompt.split('"masked_samples"')[1]
            tokens = re.findall(r'"([^"]+)"', sample_block)[:5]
        else:
            tokens = re.findall(r'"masked_value": "([^"]*)"', prompt)[:8]
        for token in tokens:
            if token and CANARY not in token:
                assert any(ch in token for ch in "*＊xX"), f"unmasked sample in prompt: {token}"


def test_exception_paths_do_not_leak(fixture_path: Path, tmp_path: Path):
    binary = tmp_path / "broken.csv"
    binary.write_bytes(b"\x00\x01\x02" + "010-9876-5432".encode())
    stream, handler, root, previous = capture_logs()
    try:
        try:
            analyse_file(binary)
        except Exception:
            pass
        ragged = tmp_path / "ragged.csv"
        ragged.write_text("NAME,RESIDENT\n김민준9001011234567,9001011234567\n", encoding="utf-8")
        analyse_file(ragged)
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)
    hits = scan(stream.getvalue())
    assert not hits, f"raw personal data in error output: {hits}"


def test_llm_exception_is_not_fatal(fixture_path: Path):
    """AI failure must not destroy the rule result, and must not print values."""
    from app.ai.llm_manager import LocalLLMEngine

    engine = LocalLLMEngine()

    class FailingLLM:
        def __call__(self, prompt, **kwargs):
            raise RuntimeError("simulated llama.cpp inference failure")

    engine.llm = FailingLLM()
    engine.load = lambda: True
    stream, handler, root, previous = capture_logs()
    try:
        result = analyse_file(fixture_path, llm=engine)
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)
    assert result.results, "rule based result disappeared when AI raised"
    hits = scan(stream.getvalue())
    assert not hits, f"raw personal data in AI failure output: {hits}"


def test_json_report_and_diagnostic_dump_are_clean(fixture_path: Path):
    """`--json` / `--report-json` payloads and the diagnostics dump carry no raw values."""
    diagnostics = Diagnostics(enabled=True)
    result = analyse_file(fixture_path, diagnostics=diagnostics)

    for payload in (json.dumps(result.as_dict(), ensure_ascii=False),
                    json.dumps({"events": diagnostics.events}, ensure_ascii=False)):
        hits = scan(payload)
        assert not hits, f"raw personal data in JSON report: {hits}"
