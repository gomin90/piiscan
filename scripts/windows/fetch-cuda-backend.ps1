# Privacy Scanner - NVIDIA GPU(CUDA) backend 준비 (선택)
# CPU bundle에는 ggml-cpu DLL만 들어 있다. RTX 5060 같은 NVIDIA GPU 추론은
# llama.cpp 공식 CUDA binary의 ggml-cuda.dll이 필요하다.
#
# 1) 오프라인: bundle안의 llama-b12556-bin-win-cuda-12.4-x64.zip을 추출한다 (인터넷 불필요).
#    powershell -ExecutionPolicy Bypass -File scripts\windows\install-cuda-backend.ps1
# 2) 온라인: 이 script이 공식 asset을 한 번 내려받아 SHA256 확인 후 추출한다.
#
#   powershell -ExecutionPolicy Bypass -File scripts\windows\fetch-cuda-backend.ps1
param(
    [string]$BundleRoot = ".",
    [string]$Tag = "b12556"
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path $BundleRoot).Path
$out = Join-Path $root "runtime\windows"
New-Item -ItemType Directory -Force -Path $out | Out-Null

$assetUrl = "https://github.com/ggml-org/llama.cpp/releases/download/$Tag/llama-$Tag-bin-win-cuda-12.4-x64.zip"
$expectedSha = "e18df7a7189e1446ca338aa4f04529acb4dce7537164baed2fbc624e48864932"
$zip = Join-Path $out "llama-$Tag-bin-win-cuda-12.4-x64.zip"

Write-Host "Downloading official llama.cpp CUDA backend ($Tag) once ..."
Write-Host "  $assetUrl"
Write-Host "This step needs internet. After it completes the app runs offline."

try {
    Invoke-WebRequest -Uri $assetUrl -OutFile $zip -MaximumRedirects 5
} catch {
    Write-Host "ERROR: download failed. Check proxy/firewall policy."
    exit 1
}

$hash = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLower()
if ($hash -ne $expectedSha) {
    Write-Host "ERROR: SHA256 mismatch. expected $expectedSha got $hash"
    Write-Host "The llama.cpp release may have changed; verify the asset on the release page."
    exit 1
}
Write-Host "SHA256 verified: $hash"

Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [System.IO.Compression.ZipFile]::OpenRead($zip)
$taken = 0
foreach ($entry in $archive.Entries) {
    if ($entry.Name -like "*.dll") {
        $dest = Join-Path $out $entry.Name
        $entry.Open().Close()
        [System.IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $dest, $true)
        $taken++
    }
}
$archive.Dispose()
Write-Host "Extracted $taken DLLs into runtime\windows (ggml-cuda.dll replaces the CPU backend at runtime)."
Write-Host "Verify afterwards: scripts\windows\verify-offline-bundle.ps1"
