# Privacy Scanner - Windows EXE 빌드 (사용자 side)
# Mac에서 준비한 wheelhouse로 offline build한다. PyInstaller는 반드시 Windows에서 실행해야 한다.
#
#   powershell -ExecutionPolicy Bypass -File scripts\windows\build-exe.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\windows\build-exe.ps1 -RuleOnly
param(
    [string]$BundleRoot = ".",
    [string]$PythonExe = "python",
    [switch]$RuleOnly,
    [switch]$SkipVerify
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path $BundleRoot).Path
cd $root

if (-not (Get-Command $PythonExe -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: python not found. Install Python 3.12 x64."
    exit 1
}

if (-not $SkipVerify) {
    Write-Host "--- 1. bundle integrity ---"
    & powershell -ExecutionPolicy Bypass -File "scripts\windows\verify-offline-bundle.ps1"
    if ($LASTEXITCODE -ne 0) { Write-Host "Bundle verification failed. Fix missing files before building."; exit 1 }
}

Write-Host "--- 2. offline dependency install ---"
& $PythonExe -m pip install --no-index --find-links "vendor\wheels" -r "requirements\requirements-win.txt"
if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: offline install failed"; exit 1 }

Write-Host "--- 3. PyInstaller build ---"
$buildArgs = @("tools\build.py", "--target", "windows", "--skip-tests")
if ($RuleOnly) { $buildArgs += "--rule-only" }
& $PythonExe $buildArgs
if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: build failed (exit $LASTEXITCODE)"; exit 1 }

$exe = Join-Path $root "dist\PrivacyScanner.exe"
if (Test-Path -LiteralPath $exe) {
    $size = (Get-Item -LiteralPath $exe).Length
    Write-Host ("[ok] dist\PrivacyScanner.exe  {0} MB  sha256={1}" -f [math]::Round($size/1MB,1),
        (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLower())
    Write-Host "Run: scripts\windows\run.bat --file samples\windows_sample.csv --debug"
}
