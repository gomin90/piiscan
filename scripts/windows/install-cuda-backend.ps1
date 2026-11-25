# Privacy Scanner - offline NVIDIA(CUDA) backend installation
#
# The CUDA binaries of llama.cpp are already inside the bundle:
#   vendor\llama-windows\llama-b12556-bin-win-cuda-13.5-x64.zip   (229,295,176 B)  Blackwell (RTX 50xx, sm_120)
#   vendor\llama-windows\llama-b12556-bin-win-cuda-12.4-x64.zip   (364,747,106 B)  Maxwell..Hopper (sm_50..sm_90)
# This script extracts the one that matches the GPU in this machine into runtime\windows.
# No internet is required.
#
#   powershell -ExecutionPolicy Bypass -File scripts\windows\install-cuda-backend.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\windows\install-cuda-backend.ps1 -CudaVersion 13.3
#   powershell -ExecutionPolicy Bypass -File scripts\windows\install-cuda-backend.ps1 -CudaVersion 12.4
#
# CUDA 13.3 is the toolkit most RTX 50 series machines ship with. llama.cpp only publishes
# CUDA 12.4 and CUDA 13.5 *prebuilt* binaries, so -CudaVersion 13.3 uses the CUDA 13.5 build
# (identical ggml ABI, same cudart64_13.dll / cublas64_13.dll / cublasLt64_13.dll runtime) and
# validates the local CUDA 13 runtime DLLs. If you want a backend compiled against the exact
# 13.3 toolchain, run scripts\windows\build-cuda-133-backend.ps1.
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

function Get-NvidiaCapability {
    if (-not (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) { return "" }
    return (nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>$null) | Select-Object -First 1
}

function Get-InstalledCudaVersions {
    # Every CUDA toolkit this machine has, e.g. v13.3.
    $found = New-Object System.Collections.Generic.List[string]
    $cudaRoot = if ($env:CUDA_PATH) { $env:CUDA_PATH } else { "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA" }
    if (Test-Path -LiteralPath $cudaRoot) {
        foreach ($v in (Get-ChildItem -LiteralPath $cudaRoot -Directory)) {
            if ($v.Name -match '^v?(\d+\.\d+)') { $found.Add($Matches[1]) }
        }
    }
    return $found
}

# Prebuilt llama.cpp assets available in the offline bundle.
$assets = @{
    "13.5" = @{ zip = "llama-$Tag-bin-win-cuda-13.5-x64.zip"; sha = "dde6d3f922b2225574ba32fe4bad227fb4784a8a5ebabc78c69fb3da7979ae23"; archs = "sm_86, sm_89, sm_103, sm_120, sm_121"; runtime = "cudart64_13.dll, cublas64_13.dll, cublasLt64_13.dll" }
    "12.4" = @{ zip = "llama-$Tag-bin-win-cuda-12.4-x64.zip"; sha = "e18df7a7189e1446ca338aa4f04529acb4dce7537164baed2fbc624e48864932"; archs = "sm_50, sm_61, sm_70, sm_75, sm_80, sm_90"; runtime = "cudart64_12.dll, cublas64_12.dll, cublasLt64_12.dll" }
}
# CUDA 13.3 has no official prebuilt asset; the 13.x build is the ABI-compatible one for it.
$aliases = @{ "13.3" = "13.5"; "13" = "13.5" }

$installed = Get-InstalledCudaVersions
if ($installed) { Write-Host "CUDA toolkits installed on this machine: $($installed -join ', ')" }

if ($CudaVersion -eq "auto") {
    $capability = Get-NvidiaCapability
    if (-not $capability) {
        Write-Host "compute capability unknown; choosing the Blackwell (CUDA 13) backend."
        $CudaVersion = "13.3"
    } else {
        $numeric = [double]($capability -replace '[^0-9.]', '')
        Write-Host "GPU compute capability: $capability"
        # Blackwell and newer need CUDA >= 12.8; anything older stays on the 12.4 build.
        $CudaVersion = if ($numeric -ge 10.0) { "13.3" } else { "12.4" }
    }
}

if ($aliases.ContainsKey($CudaVersion)) {
    Write-Host "-CudaVersion $CudaVersion -> using the llama.cpp CUDA 13 build ($($aliases[$CudaVersion]))"
    $assetKey = $aliases[$CudaVersion]
} elseif ($assets.ContainsKey($CudaVersion)) {
    $assetKey = $CudaVersion
} else {
    Write-Host "ERROR: unsupported -CudaVersion $CudaVersion (use 12.4, 13.3 or 13.5)"
    exit 1
}

$capability = Get-NvidiaCapability
if ($capability) {
    $numeric = [double]($capability -replace '[^0-9.]', '')
    if ($numeric -ge 10.0 -and $assetKey -eq "12.4") {
        Write-Host "ERROR: this GPU is $capability (Blackwell/RTX 50 series). The CUDA 12.4 backend"
        Write-Host "has no sm_120 kernels and needs cublas64_12.dll, which a CUDA 13 install does not"
        Write-Host "provide. Use -CudaVersion 13.3 instead."
        exit 1
    }
}

$asset = $assets[$assetKey]
$src = Join-Path $root "vendor\llama-windows\$($asset.zip)"
if (-not (Test-Path -LiteralPath $src)) {
    Write-Host "ERROR: $($asset.zip) is not in the bundle. Fetch it with scripts\windows\fetch-cuda-backend.ps1 -CudaVersion $assetKey"
    exit 1
}

$hash = (Get-FileHash -LiteralPath $src -Algorithm SHA256).Hash.ToLower()
if ($hash -ne $asset.sha) {
    Write-Host "ERROR: CUDA zip SHA256 mismatch (got $hash, expected $($asset.sha))."
    Write-Host "Re-download with scripts\windows\fetch-cuda-backend.ps1 -CudaVersion $assetKey"
    exit 1
}
Write-Host "CUDA $assetKey asset integrity OK ($hash) - supports $($asset.archs)"
Write-Host "Required CUDA runtime DLLs: $($asset.runtime)"

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
# CUDA 13.x keeps its runtime DLLs in bin\x64; CUDA 11/12 in bin.
$cudaRoot = if ($env:CUDA_PATH) { $env:CUDA_PATH } else { "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA" }
$copied = 0
if (Test-Path -LiteralPath $cudaRoot) {
    foreach ($v in (Get-ChildItem -LiteralPath $cudaRoot -Directory)) {
        foreach ($binName in @("bin\x64", "bin")) {
            $bin = Join-Path $v.FullName $binName
            if (-not (Test-Path -LiteralPath $bin)) { continue }
            foreach ($name in @("cudart64_13.dll", "cublas64_13.dll", "cublasLt64_13.dll",
                                "cublas64_12.dll", "cublasLt64_12.dll", "cudart64_12.dll")) {
                $f = Join-Path $bin $name
                if (Test-Path -LiteralPath $f) {
                    Copy-Item -LiteralPath $f -Destination $out -Force
                    Write-Host "  copied $name from $bin"
                    $copied++
                }
            }
        }
    }
}
if ($copied -eq 0) {
    Write-Host "NOTE: no CUDA runtime DLLs found - a CUDA toolkit (13.3 or 12.4) must be installed,"
    Write-Host "      or the NVIDIA driver's redistributable DLLs must be on PATH."
    Write-Host "      Without cublas64_13.dll / cublasLt64_13.dll ggml-cuda.dll cannot initialise:"
    Write-Host "      scripts\windows\fetch-cuda-backend.ps1 -CudaVersion 13.5"
}

Write-Host "Extracted $taken DLLs into runtime\windows"
Write-Host "runtime\windows now holds:"
foreach ($f in (Get-ChildItem -LiteralPath $out -Filter *.dll)) {
    Write-Host ("  {0,-30} {1,8} MB" -f $f.Name, [math]::Round($f.Length / 1MB, 1))
}

$exe = Join-Path $root "dist\PrivacyScanner.exe"
if (Test-Path -LiteralPath $exe) {
    Write-Host "NOTE: dist\PrivacyScanner.exe was built before this install - PyInstaller embedded"
    Write-Host "      runtime\windows at build time, so the EXE still contains only the CPU backend."
    Write-Host "      Re-run scripts\windows\build-exe.ps1, or set GGML_BACKEND_DIR to this runtime\windows."
}

Write-Host "Verify with: scripts\windows\gpu-check.ps1"
