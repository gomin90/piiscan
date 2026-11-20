# Privacy Scanner - offline NVIDIA(CUDA) backend installation
#
# The CUDA binaries of llama.cpp are already inside the bundle:
#   vendor\llama-windows\llama-b12556-bin-win-cuda-13.5-x64.zip   (229,295,176 B)  Blackwell (RTX 50xx, sm_120)
#   vendor\llama-windows\llama-b12556-bin-win-cuda-12.4-x64.zip   (364,747,106 B)  Maxwell..Hopper (sm_50..sm_90)
# This script extracts the one that matches the GPU in this machine into runtime\windows.
# No internet is required.
#
#   powershell -ExecutionPolicy Bypass -File scripts\windows\install-cuda-backend.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\windows\install-cuda-backend.ps1 -CudaVersion 12.4
#
# Read scripts\windows\gpu-check.ps1 output first: an RTX 50 series GPU cannot use the
# CUDA 12.4 backend (no sm_120 kernels, and it needs cublas64_12.dll which a CUDA 13
# install does not provide).
param(
    [string]$BundleRoot = ".",
    [string]$CudaVersion = "auto",
    [string]$Tag = "b12556"
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path $BundleRoot).Path
$out = Join-Path $root "runtime\windows"
if (-not (Test-Path -LiteralPath $out)) { New-Item -ItemType Directory -Force -Path $out | Out-Null }

$assets = @{
    "13.5" = @{ zip = "llama-$Tag-bin-win-cuda-13.5-x64.zip"; sha = "dde6d3f922b2225574ba32fe4bad227fb4784a8a5ebabc78c69fb3da7979ae23"; archs = "sm_86, sm_89, sm_103, sm_120, sm_121" }
    "12.4" = @{ zip = "llama-$Tag-bin-win-cuda-12.4-x64.zip"; sha = "e18df7a7189e1446ca338aa4f04529acb4dce7537164baed2fbc624e48864932"; archs = "sm_50, sm_61, sm_70, sm_75, sm_80, sm_90" }
}

if ($CudaVersion -eq "auto") {
    $capability = ""
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
        $capability = (nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>$null) | Select-Object -First 1
    }
    if (-not $capability) {
        Write-Host "compute capability unknown; choosing the Blackwell (CUDA 13.5) backend."
        $CudaVersion = "13.5"
    } else {
        $numeric = [double]($capability -replace '[^0-9.]', '')
        Write-Host "GPU compute capability: $capability"
        # Blackwell and newer need CUDA >= 12.8; llama.cpp ships that as the 13.5 build.
        $CudaVersion = if ($numeric -ge 10.0) { "13.5" } else { "12.4" }
    }
}

if (-not $assets.ContainsKey($CudaVersion)) {
    Write-Host "ERROR: unsupported -CudaVersion $CudaVersion (use 12.4 or 13.5)"
    exit 1
}

$asset = $assets[$CudaVersion]
$src = Join-Path $root "vendor\llama-windows\$($asset.zip)"
if (-not (Test-Path -LiteralPath $src)) {
    Write-Host "ERROR: $($asset.zip) is not in the bundle. Fetch it with scripts\windows\fetch-cuda-backend.ps1 -CudaVersion $CudaVersion"
    exit 1
}

$hash = (Get-FileHash -LiteralPath $src -Algorithm SHA256).Hash.ToLower()
if ($hash -ne $asset.sha) {
    Write-Host "ERROR: CUDA zip SHA256 mismatch (got $hash, expected $($asset.sha))."
    Write-Host "Re-download with scripts\windows\fetch-cuda-backend.ps1 -CudaVersion $CudaVersion"
    exit 1
}
Write-Host "CUDA $CudaVersion asset integrity OK ($hash) - supports $($asset.archs)"

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

# cuBLAS is linked dynamically by ggml-cuda.dll. If a CUDA toolkit is installed, make sure
# the matching cuBLAS DLLs are resolvable by copying them next to the scorer.
$cudaRoot = if ($env:CUDA_PATH) { $env:CUDA_PATH } else { "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA" }
if (Test-Path -LiteralPath $cudaRoot) {
    foreach ($v in (Get-ChildItem -LiteralPath $cudaRoot -Directory)) {
        $bin = Join-Path $v.FullName "bin"
        if (-not (Test-Path -LiteralPath $bin)) { continue }
        foreach ($name in @("cublas64_13.dll", "cublasLt64_13.dll", "cublas64_12.dll", "cublasLt64_12.dll", "cudart64_12.dll")) {
            $f = Join-Path $bin $name
            if (Test-Path -LiteralPath $f) {
                Copy-Item -LiteralPath $f -Destination $out -Force
                Write-Host "  copied $name from $bin"
            }
        }
    }
}

Write-Host "Extracted $taken DLLs into runtime\windows"
Write-Host "Verify with: scripts\windows\gpu-check.ps1"
