# Privacy Scanner - Windows offline dependency install
# Installs from vendor\wheels only (no internet). Python 3.12 / 3.13 / 3.14 x64.
#
#   powershell -ExecutionPolicy Bypass -File scripts\windows\install-offline.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\windows\install-offline.ps1 -PythonExe py
param(
    [string]$BundleRoot = ".",
    [string]$PythonExe = "python"
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path $BundleRoot).Path
$wheels = Join-Path $root "vendor\wheels"
$req = Join-Path $root "requirements\requirements-win.txt"

if (-not (Test-Path -LiteralPath $wheels)) {
    Write-Host "ERROR: wheelhouse not found: $wheels"
    exit 1
}
if (-not (Get-Command $PythonExe -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: python not found. Install Python 3.12, 3.13 or 3.14 (64-bit) first."
    exit 1
}

& $PythonExe -c "import struct,sys;print(sys.version);print('bits', struct.calcsize('P')*8)"
if ((& $PythonExe -c "import struct;print(struct.calcsize('P')*8)") -ne "64") {
    Write-Host "WARNING: 32-bit Python detected. The offline wheelhouse is win_amd64 only."
}

# numpy and PyYAML ship per-minor-version wheels (cp312, cp313, cp314). Detect the
# interpreter early so a mismatch is a clear message instead of a later
# "No module named numpy" crash.
$minor = 0
try {
    $minor = [int]((& $PythonExe -c "import sys;print(sys.version_info[1])") 2>&1)
} catch {
    $minor = 0
}
if ($minor -notin @(12, 13, 14)) {
    Write-Host "ERROR: Python 3.$minor is not covered by this wheelhouse."
    Write-Host "       numpy/PyYAML wheels included: cp312, cp313, cp314 (win_amd64)."
    Write-Host "       Install Python 3.12, 3.13 or 3.14 (64-bit) and re-run this script."
    exit 1
}
Write-Host "Python 3.$minor detected - matching wheels are present."

Write-Host "Installing offline wheels from $wheels ..."
& $PythonExe -m pip install --no-index --find-links "$wheels" -r "$req"
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: offline install failed (exit $LASTEXITCODE)."
    Write-Host "Check that vendor\wheels contains a wheel for this Python version"
    Write-Host "(e.g. numpy-2.5.5-cp313-cp313-win_amd64.whl for CPython 3.13 x64)."
    exit 1
}

# Record installed versions (no personal information is written)
& $PythonExe -m pip list --format=freeze | Select-String "PySide6|shiboken|pyinstaller|psutil|numpy|tokenizers|pytest"

$missing = (& $PythonExe -c "import importlib.util; print(','.join(m for m in ['PySide6','psutil','numpy','tokenizers','yaml'] if importlib.util.find_spec(m) is None))") 2>&1
if (($missing | Out-String).Trim() -ne "") {
    Write-Host "WARNING: still missing after install: $missing"
} else {
    Write-Host "[ok] PySide6, psutil, numpy, tokenizers, pyyaml all importable."
}
Write-Host "Done. Next: scripts\windows\build-exe.ps1  or  scripts\windows\run.bat"
