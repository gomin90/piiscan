"""Security requirements: no raw personal data in samples, logs or reports."""
from __future__ import annotations

import tempfile
from pathlib import Path

from app.export.csv_exporter import write_csv
from app.export.txt_exporter import write_txt
from app.utils.masking import is_null, looks_masked, mask_value
from app.utils.logger import Diagnostics
from app.core.analysis_service import analyse_file
from tests.fixtures.generate_fixtures import FIXTURE_DIR

RAW_PII = {
    "010-5179-2931",
    "user1@example.com",
    "690815-265681-7",
    "4660487647593823",
    "박민준",
}


def test_masking_keeps_shape_but_not_value():
    masked = mask_value("010-1234-5678")
    assert "1234" not in masked
    assert "*" in masked
    assert looks_masked("010-****-5678")
    assert not looks_masked("010-1234-5678")
    assert is_null("NULL") and is_null("") and not is_null("1001")


def test_preview_samples_are_masked():
    result = analyse_file(FIXTURE_DIR / "utf8_comma.csv")
    for finding in result.findings:
        for sample in finding["stats"]["samples"]:
            assert "*" in sample or len(sample) <= 3, f"raw value reached stats: {sample}"


def test_diagnostics_do_not_contain_row_content():
    diagnostics = Diagnostics(enabled=True)
    analyse_file(FIXTURE_DIR / "utf8_comma.csv", diagnostics=diagnostics)
    dumped = diagnostics.dump()
    assert "010-" not in dumped
    assert "홍길동" not in dumped or "user1@" not in dumped


def test_exports_do_not_contain_raw_values():
    result = analyse_file(FIXTURE_DIR / "mixed_pii.csv")
    with tempfile.TemporaryDirectory() as tmp:
        csv_path = write_csv(result.results, Path(tmp) / "results.csv")
        txt_path = write_txt([result], Path(tmp) / "report.txt")
        text = Path(csv_path).read_text() + Path(txt_path).read_text()
    for value in RAW_PII:
        assert value not in text


def test_diagnostics_do_not_contain_row_content_across_fixtures():
    diagnostics = Diagnostics(enabled=True)
    for name in ("mixed_pii.csv", "masked_encrypted.csv", "ssn_like.csv"):
        path = FIXTURE_DIR / name
        if path.exists():
            analyse_file(path, diagnostics=diagnostics)
    dumped = diagnostics.dump()
    for value in RAW_PII:
        assert value not in dumped
