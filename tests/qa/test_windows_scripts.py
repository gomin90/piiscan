"""PowerShell scripts are not checked by CI, and PowerShell does not fail gracefully on a
syntax error the way Python does. These tests keep the Windows scripts loadable.

`else if` is the classic trap: PowerShell needs `elseif`, and the parser aborts with a
"Missing closing '}'" style error that looks nothing like the actual problem.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

WINDOWS_SCRIPTS = sorted((Path(__file__).resolve().parents[2] / "scripts" / "windows").glob("*.ps1"))


def test_all_windows_scripts_exist():
    assert len(WINDOWS_SCRIPTS) >= 5, "expected the Windows helper scripts to be present"


@pytest.mark.parametrize("script", WINDOWS_SCRIPTS, ids=lambda p: p.name)
def test_no_else_if(script: Path):
    """`else if` is a PowerShell parse error."""
    offending = [
        number for number, line in enumerate(script.read_text(encoding="utf-8").splitlines(), 1)
        if "else if" in line
    ]
    assert not offending, f"{script} uses 'else if' on line(s) {offending}; PowerShell requires 'elseif'"


@pytest.mark.parametrize("script", WINDOWS_SCRIPTS, ids=lambda p: p.name)
def test_script_parses(script: Path):
    """Ask PowerShell itself for parse errors. Skipped where PowerShell is unavailable."""
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        pytest.skip("no PowerShell (pwsh/powershell) on this machine")
    command = (
        "$tokens = $null; $errors = $null; "
        f"[System.Management.Automation.Parser]::ParseFile('{script}', [ref] $tokens, [ref] $errors); "
        "if ($errors.Count -gt 0) { $errors | ForEach-Object { Write-Output $_.Message }; exit 1 } "
        "Write-Output 'syntax OK'"
    )
    result = subprocess.run([shell, "-NoProfile", "-Command", command], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, f"{script.name} has PowerShell syntax errors:\n{result.stdout}\n{result.stderr}"


@pytest.mark.parametrize("script", WINDOWS_SCRIPTS, ids=lambda p: p.name)
def test_scripts_have_balanced_braces(script: Path):
    """A cheap guard for the unclosed-block case, ignoring braces inside quoted
    strings and inside comments (an apostrophe in a comment is not a string)."""
    depth = 0
    in_string = None
    for line in script.read_text(encoding="utf-8").splitlines():
        for char in line:
            if in_string:
                if char == in_string:
                    in_string = None
                continue
            if char == "#":
                break  # rest of the line is a comment
            if char in "'\"":
                in_string = char
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth < 0:
                    break
    assert depth == 0, f"{script.name} has unbalanced braces (depth {depth})"
