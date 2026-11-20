# Privacy Scanner - offline NVIDIA(CUDA) backend installation
#
# The CUDA binary of llama.cpp is already inside the bundle:
#   vendor\llama-windows\llama-b12556-bin-win-cuda-12.4-x64.zip   (348 MB, SHA256 checked)
# This script extracts it into runtime\windows. No internet is required.
#
#   powershell -ExecutionPolicy Bypass -File scripts\windows\install-cuda-backend.ps1
#
# Only do this when diagnose.ps1 reports an NVIDIA GPU and a CUDA-capable driver.
# The CUDA asset is built for CUDA 12.4; RTX 5060 (Blackwell) support must be
# confirmed by actually running an inference.
param(
    [string]$BundleRoot = ".",
    [string]$Zip = "vendor\llama-windows\llama-b12556-bin-win-cuda-12.4-x64.zip"
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path $BundleRoot).Path
$src = Resolve-Path (Join-Path $root $Zip)
$out = Join-Path $root "runtime\windows"

$expected = "e18df7a7189e1446ca338aa4f04529acb4dce7537164baed2fbc624e48864932"
$hash = (Get-FileHash -LiteralPath $src -Algorithm SHA256).Hash.ToLower()
if ($hash -ne $expected) {
    Write-Host "ERROR: CUDA zip SHA256 mismatch (got $hash). Re-download with scripts\windows\fetch-cuda-backend.ps1."
    exit 1
}
Write-Host "CUDA zip integrity OK ($hash)"

Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [System.IO.Compression.ZipFile]::OpenRead($src)
$taken = 0
foreach ($entry in $archive.Entries) {
    if ($entry.Name -like "*.dll") {
        [System.IO.Compression.ZipFileExtensions]::ExtractToFile($entry, (Join-Path $out $entry.Name), $true)
        $taken++
    }
}
$archive.Dispose()
Write-Host "Extracted $taken DLLs (llama.dll, ggml.dll, ggml-base.dll, ggml-cuda.dll, ggml-cpu-*) into runtime\windows"
Write-Host "Re-run diagnose.ps1 and then the CPU/GPU inference test."
