# Privacy Scanner - Windows 오프라인 의존성 설치
# internet 없이 vendor\wheels 만으로 설치한다. Python 3.12 x64 권장.
#
#   powershell -ExecutionPolicy Bypass -File scripts\windows\install-offline.ps1
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
    Write-Host "ERROR: python not found. Install Python 3.12 x64 first."
    exit 1
}

& $PythonExe -c "import struct,sys;print(sys.version);print('bits', struct.calcsize('P')*8)"
if ((& $PythonExe -c "import struct;print(struct.calcsize('P')*8)") -ne "64") {
    Write-Host "WARNING: 32-bit Python detected. The offline wheelhouse is win_amd64 only."
}

Write-Host "Installing offline wheels from $wheels ..."
& $PythonExe -m pip install --no-index --find-links "$wheels" -r "$req"
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: offline install failed (exit $LASTEXITCODE). Check that every wheel in vendor\wheels is present."
    exit 1
}

# 설치된 패키지와 버전을 기록 (개인정보 없음)
& $PythonExe -m pip list --format=freeze | Select-String "PySide6|shiboken|pyinstaller|psutil|numpy|tokenizers|pytest"
Write-Host "Done. Next: scripts\windows\build-exe.ps1"
