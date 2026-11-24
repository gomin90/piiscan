# Privacy Scanner - CUDA 13.3 backend build for NVIDIA RTX 50 series (sm_120)
#
# llama.cpp only ships CUDA 12.4 and CUDA 13.5 prebuilt binaries. A machine with the
# CUDA Toolkit 13.3 needs a backend compiled against that toolchain; this script builds
# ggml-cuda.dll (+ ggml.dll / llama.dll) from the vendored llama.cpp source.
#
# Requirements on the Windows PC (no internet needed after the bundle is copied):
#   - Visual Studio 2022 Build Tools or MSVC cl.exe on PATH
#   - CUDA Toolkit 13.3 (nvcc on PATH, or CUDA_PATH set)
#   - CMake >= 3.21
#   - NVIDIA driver that supports CUDA 13.3 (Studio 5xx+/GeForce RTX 40+/50+)
#
#   powershell -ExecutionPolicy Bypass -File scripts\windows\build-cuda-133-backend.ps1
#
# The produced DLLs are copied into runtime\windows, which is where the scorer looks.
param(
    [string]$BundleRoot = ".",
    [string]$SourceDir = "vendor\llama.cpp",
    [string]$BuildDir = "vendor\llama.cpp\build-cuda-133",
    [string]$CudaArch = "120",
    [string]$CudaVersion = "13.3"
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path $BundleRoot).Path
$src = Join-Path $root $SourceDir
$build = Join-Path $root $BuildDir
$out = Join-Path $root "runtime\windows"

foreach ($tool in @("cmake", "nvcc")) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        Write-Host "ERROR: $tool is required on PATH to build the CUDA 13.3 backend."
        exit 1
    }
}

$nvccVersion = (nvcc --version) -join " "
if ($nvccVersion -notmatch 'release (\d+\.\d+)') {
    Write-Host "ERROR: cannot read nvcc version: $nvccVersion"
    exit 1
}
$actual = $Matches[1]
Write-Host "nvcc reports CUDA $actual (target CUDA $CudaVersion)"
if ($actual -notmatch '^13\.') {
    Write-Host "ERROR: CUDA 13.x toolchain required for an sm_$CudaArch Blackwell backend."
    exit 1
}

Write-Host "Configuring llama.cpp for CUDA $actual / sm_$CudaArch, dynamic backend"
if (-not (Test-Path -LiteralPath $build)) { New-Item -ItemType Directory -Force -Path $build | Out-Null }

cmake -B $build -S $src `
    -GGML_CUDA=ON `
    -GGML_BACKEND_DL=ON `
    -CMAKE_CUDA_ARCHITECTURES=$CudaArch `
    -DCMAKE_BUILD_TYPE=Release

cmake --build $build --config Release -j

$produced = Get-ChildItem -LiteralPath $build -Recurse -Filter "ggml-cuda.dll"
if (-not $produced) {
    Write-Host "ERROR: the build did not produce ggml-cuda.dll"
    exit 1
}
foreach ($dll in $produced) {
    Copy-Item -LiteralPath $dll.FullName -Destination $out -Force
    Write-Host "  installed $($dll.Name) ($([math]::Round($dll.Length/1MB,1)) MB) -> runtime\windows"
}

# CUDA 13 runtime DLLs must sit next to the backend or on PATH.
$cudaRoot = if ($env:CUDA_PATH) { $env:CUDA_PATH } else { "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA" }
foreach ($name in @("cudart64_13.dll", "cublas64_13.dll", "cublasLt64_13.dll")) {
    $found = $null
    if (Test-Path -LiteralPath $cudaRoot) {
        foreach ($v in (Get-ChildItem -LiteralPath $cudaRoot -Directory)) {
            foreach ($binName in @("bin\x64", "bin")) {
                $candidate = Join-Path (Join-Path $v.FullName $binName) $name
                if (Test-Path -LiteralPath $candidate) { $found = $candidate; break }
            }
            if ($found) { break }
        }
    }
    if ($found) {
        Copy-Item -LiteralPath $found -Destination $out -Force
        Write-Host "  copied $name from the CUDA toolkit"
    } else {
        Write-Host "  WARNING: $name not found - GPU inference will fall back to CPU."
    }
}

Write-Host "Verify with: scripts\windows\gpu-check.ps1"
