# Privacy Scanner - 오프라인 번들 무결성 검증 (Windows)
# manifests/offline-bundle-manifest.json 의 SHA256 로 모든 파일을 대조한다.
#
#   powershell -ExecutionPolicy Bypass -File scripts\windows\verify-offline-bundle.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\windows\verify-offline-bundle.ps1 -SmokeTest
param(
    [string]$BundleRoot = ".",
    [string]$Manifest = "manifests\offline-bundle-manifest.json",
    [string]$OutputDir = "results\windows",
    [switch]$SmokeTest
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path $BundleRoot).Path
$manifestPath = Join-Path $root $Manifest

if (-not (Test-Path -LiteralPath $manifestPath)) {
    Write-Host "ERROR: manifest not found: $manifestPath"
    Write-Host "Run 'python tools\make_bundle_manifest.py' on the development machine first."
    exit 1
}

# ReadAllText + explicit ConvertFrom-Json avoids pipeline quirks on some PowerShell versions
$text = [System.IO.File]::ReadAllText($manifestPath)
$manifestObj = ConvertFrom-Json -InputObject $text
$ok = 0
$mismatch = @()
$missing = @()
$checkedBytes = 0

foreach ($entry in $manifestObj.files) {
    $path = Join-Path $root ($entry.path -replace '/', '\')
    if (-not (Test-Path -LiteralPath $path)) {
        $missing += $entry.path
        continue
    }
    $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLower()
    $size = (New-Object System.IO.FileInfo($path)).Length
    if ($hash -ne $entry.sha256) {
        $mismatch += [ordered]@{ path = $entry.path; expected = $entry.sha256; actual = $hash; size = $size }
    } else {
        $ok++
        $checkedBytes += $size
    }
}

# --- mandatory component presence -------------------------------------------
$required = @(
    "runtime\windows\jev-score.exe",
    "runtime\windows\jev-score-v2.exe",
    "runtime\windows\llama.dll",
    "runtime\windows\ggml.dll",
    "runtime\windows\ggml-base.dll",
    "runtime\windows\libomp.dll",
    "models\jev-style\0.8b-v3\Jev-Style-0.8B-Decision-v3-Q4_K_M.gguf",
    "models\jev-style\0.8b-v3\jev_style_decision_gguf.py",
    "models\jev-style\0.8b-v3\tokenizer\tokenizer.json",
    "models\jev-style\0.8b-v3\readout_config.json",
    "models\jev-style\2b-v4\Jev-Style-2B-Decision-v4-Q4_K_M.gguf",
    "models\jev-style\2b-v4\jev_style_decision_gguf.py",
    "models\jev-style\2b-v4\tokenizer\tokenizer.json",
    "models\jev-style\2b-v4\readout_config.json"
)
$missingRequired = @()
foreach ($r in $required) {
    if (-not (Test-Path -LiteralPath (Join-Path $root $r))) { $missingRequired += $r }
}

# --- optional scorer smoke test (real decision, synthetic data) -------------
$smoke = $null
if ($SmokeTest) {
    $pythonExe = $null
    foreach ($candidate in @("python", "py")) {
        if (Get-Command $candidate -ErrorAction SilentlyContinue) { $pythonExe = $candidate; break }
    }
    if (-not $pythonExe) {
        $smoke = [ordered]@{ status = "skipped"; reason = "python not found" }
    } else {
        foreach ($release in @("0.8b-v3", "2b-v4")) {
            try {
                $output = & $pythonExe "tools\jev_smoke.py" --model $release --output "results\windows\smoke_$release.json" 2>&1
                $smokeText = ($output | Out-String)
                Write-Host "--- smoke test $release ---"
                Write-Host $smokeText
            } catch {
                Write-Host "smoke test failed for $release : $_"
            }
        }
        $smoke = [ordered]@{ status = "run"; see = "results\windows\smoke_*.json" }
    }
}

# --- report ---------------------------------------------------------------
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$stamp = (Get-Date).ToString("yyyyMMdd_HHmmss")
$report = [ordered]@{
    tool              = "piiscan-bundle-verify/1.0"
    verified_at       = (Get-Date).ToString("yyyy-MM-ddTHH:mm:sszzz")
    manifest          = $manifestPath
    manifest_files    = $manifestObj.file_count
    verified_ok       = $ok
    mismatches        = $mismatch
    missing_files     = $missing
    missing_required  = $missingRequired
    verified_bytes    = $checkedBytes
    smoke_test        = $smoke
    pass              = ($mismatch.Count -eq 0 -and $missing.Count -eq 0 -and $missingRequired.Count -eq 0)
}
$reportPath = Join-Path $OutputDir "bundle_verify_$stamp.json"
$report | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $reportPath -Encoding UTF8

Write-Host "Verified $ok / $($manifestObj.file_count) files ($([math]::Round($checkedBytes/1MB,1)) MB)"
if ($mismatch.Count) { Write-Host "HASH MISMATCH: $($mismatch.Count)"; $mismatch | ForEach-Object { Write-Host "  $($_.path)" } }
if ($missing.Count)   { Write-Host "MISSING: $($missing.Count)"; $missing | ForEach-Object { Write-Host "  $_" } }
if ($missingRequired.Count) { Write-Host "MISSING REQUIRED: $($missingRequired -join ', ')" }
Write-Host "Result: $(if ($report.pass) { 'PASS' } else { 'FAIL' })  (report: $reportPath)"
if (-not $report.pass) { exit 1 }
