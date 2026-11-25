# Privacy Scanner - Windows native Jev-Style scorer rebuild (optional)
# The shipped runtime\windows\jev-score*.exe was cross-built on macOS with MinGW and
# has NOT been executed on Windows. This script rebuilds it natively with MSVC+CMake
# so the scorer is produced on the target platform.
#
# Requires: Visual Studio 2022+ (MSVC), CMake >= 3.21, git.
#
#   powershell -ExecutionPolicy Bypass -File scripts\windows\build-jev-score.ps1
param(
    [string]$BundleRoot = ".",
    [string]$Tag = "b12556",
    [string]$SourceDir = "vendor\llama.cpp",
    [switch]$GPU
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path $BundleRoot).Path
cd $root

foreach ($tool in @("cmake", "git")) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        Write-Host "ERROR: $tool is required to rebuild the scorer natively."
        exit 1
    }
}

if (-not (Test-Path -LiteralPath $SourceDir)) {
    Write-Host "Cloning llama.cpp $Tag into $SourceDir (needs internet once) ..."
    git clone --depth 1 --branch $Tag https://github.com/ggml-org/llama.cpp.git $SourceDir
    if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: clone failed"; exit 1 }
}

$build = Join-Path $SourceDir "build-native"
$flags = @("-DCMAKE_BUILD_TYPE=Release", "-DLLAMA_BUILD_TESTS=OFF", "-DLLAMA_BUILD_EXAMPLES=OFF", "-DLLAMA_BUILD_SERVER=OFF", "-DLLAMA_CURL=OFF")
if ($GPU) { $flags += "-DGGML_CUDA=ON" }

Write-Host "Configuring llama.cpp (GPU=$GPU) ..."
& cmake -S $SourceDir -B $build $flags
if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: cmake configure failed"; exit 1 }
& cmake --build $build --config Release -j
if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: llama.cpp build failed"; exit 1 }

$include = Join-Path $SourceDir "include"
$ggmlInclude = Join-Path $SourceDir "ggml"
$libs = Join-Path $build "bin"

foreach ($pair in @(
    @{ release = "0.8b-v3";  src = "jev_score.cpp";    out = "jev-score.exe" },
    @{ release = "2b-v4";    src = "jev_score_v2.cpp"; out = "jev-score-v2.exe" }
)) {
    $src = Join-Path $root "models\jev-style\$($pair.release)\$($pair.src)"
    $obj = Join-Path $libs $pair.out
    Write-Host "Building $($pair.out) ..."
    & cl /O2 /std:c++17 /EHsc /I"$include" /I"$ggmlInclude" `
        "$src" /link "/LIBPATH:$libs" llama.lib ggml.lib ggml-base.lib `
        /OUT:$obj
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: MSVC build of $($pair.out) failed. Report the compiler output."
        exit 1
    }
    # The app finds the scorer in runtime\windows, where the llama.cpp DLLs already sit.
    # Copy it there so llama.cpp can load its backends from the exe's own directory.
    $dest = Join-Path $root "runtime\windows\$($pair.out)"
    Copy-Item -LiteralPath $obj -Destination $dest -Force
    Write-Host ("  built {0}  sha256={1}" -f $obj,
        (Get-FileHash -LiteralPath $obj -Algorithm SHA256).Hash.ToLower())
    Write-Host "  copied to $dest (the app looks here)"
}

Write-Host "Native scorer built. Re-run scripts\windows\verify-offline-bundle.ps1 and record the new hashes."
