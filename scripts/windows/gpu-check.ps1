# Privacy Scanner - NVIDIA/CUDA backend forensics
#
# Answers, on the machine that is failing, why "GPU detected" still ends up as CPU inference.
# It checks the six things that each have to be true, and prints the exact llama.cpp error:
#   1. is an NVIDIA GPU there, and what is the driver / CUDA version
#   2. is ggml-cuda.dll installed next to the scorer at all
#   3. which CUDA runtime DLLs does that ggml-cuda.dll import (12.x vs 13.x)
#   4. are those DLLs resolvable from the exe directory / PATH / CUDA_PATH
#   5. which GPU architectures (sm_XX) are compiled into it, and is the GPU's arch one of them
#   6. what does the scorer binary itself say when it tries to load the GPU
#
#   powershell -ExecutionPolicy Bypass -File scripts\windows\gpu-check.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\windows\gpu-check.ps1 -Model models\jev-style\2b-v4\Jev-Style-2B-Decision-v4-Q4_K_M.gguf
param(
    [string]$RuntimeDir = "runtime\windows",
    [string]$Model = "models\jev-style\2b-v4\Jev-Style-2B-Decision-v4-Q4_K_M.gguf",
    [string]$Scorer = "jev-score-v2.exe"
)

$ErrorActionPreference = "Stop"
$runtime = (Resolve-Path $RuntimeDir).Path

function Read-CudaStrings([string]$path) {
    # Scan the PE import table / string pool of a DLL for CUDA runtime names and sm_XX targets
    # without needing dumpbin. Chunked so a 789 MB ggml-cuda.dll does not need 789 MB of RAM.
    $cudaDlls = New-Object System.Collections.Generic.List[string]
    $archs = New-Object System.Collections.Generic.List[string]
    $stream = [System.IO.File]::OpenRead($path)
    try {
        $buffer = New-Object byte[] (1MB)
        $carry = New-Object byte[] 16
        while (($read = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
            $chunk = New-Object byte[] ($carry.Length + $read)
            [Array]::Copy($carry, 0, $chunk, 0, $carry.Length)
            [Array]::Copy($buffer, 0, $chunk, $carry.Length, $read)
            $text = [System.Text.Encoding]::ASCII.GetString($chunk)
            foreach ($match in [regex]::Matches($text, 'cudart64_\d+\.dll|cublas64_\d+\.dll|cublasLt64_\d+\.dll|nvcuda\.dll')) {
                if (-not $cudaDlls.Contains($match.Value)) { $cudaDlls.Add($match.Value) }
            }
            foreach ($match in [regex]::Matches($text, 'sm_\d{2,3}')) {
                if (-not $archs.Contains($match.Value)) { $archs.Add($match.Value) }
            }
            $carry = $chunk[($chunk.Length - 16)..($chunk.Length - 1)]
        }
    } finally {
        $stream.Close()
    }
    return @{ dlls = $cudaDlls; archs = $archs }
}

Write-Host "=== 1. GPU and driver ==="
$gpu = Get-CimInstance -ClassName Win32_VideoController
foreach ($g in $gpu) {
    Write-Host ("  {0} | driver {1} | VRAM {2} MB" -f $g.Name, $g.DriverVersion, [int]($g.AdapterMemory / 1MB))
}
$smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if ($smi) {
    $header = (nvidia-smi | Select-Object -First 6) -join "`n"
    $cudaVersion = if ($header -match 'CUDA Version:\s*([0-9.]+)') { $Matches[1] } else { "unknown" }
    $capability = (nvidia-smi --query-gpu=name,compute_cap --format=csv,noheader 2>$null)
    Write-Host "  nvidia-smi CUDA version: $cudaVersion"
    if ($capability) { Write-Host "  compute capability: $capability" }
    $driverCuda = if ($header -match 'Driver Version:\s*([0-9.]+)') { $Matches[1] } else { "" }
    Write-Host "  driver: $driverCuda"
} else {
    Write-Host "  nvidia-smi not found -> NVIDIA driver is likely not installed"
}

Write-Host "=== 2. Backend DLLs installed in $runtime ==="
$dlls = Get-ChildItem -LiteralPath $runtime -Filter *.dll | Select-Object Name, @{n='MB';e={[math]::Round($_.Length/1MB,1)}}
foreach ($d in $dlls) { Write-Host ("  {0,-28} {1,8} MB" -f $d.Name, $d.MB) }
$cudaPath = Join-Path $runtime "ggml-cuda.dll"
if (-not (Test-Path -LiteralPath $cudaPath)) {
    Write-Host "  ggml-cuda.dll MISSING -> llama.cpp has no CUDA backend, so inference is CPU-only."
    Write-Host "  Fix: scripts\windows\install-cuda-backend.ps1"
    exit 0
}

Write-Host "=== 3. What ggml-cuda.dll needs ==="
$info = Read-CudaStrings $cudaPath
Write-Host ("  CUDA runtime DLLs imported: {0}" -f ($info.dlls -join ", "))
Write-Host ("  GPU architectures compiled in: {0}" -f ($info.archs -join ", "))

Write-Host "=== 4. Can those DLLs be found? ==="
$searchDirs = @($runtime)
foreach ($entry in ($env:PATH -split ';')) { if ($entry) { $searchDirs += $entry } }
$cudaRoot = if ($env:CUDA_PATH) { $env:CUDA_PATH } else { "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA" }
if (Test-Path -LiteralPath $cudaRoot) {
    foreach ($v in (Get-ChildItem -LiteralPath $cudaRoot -Directory)) { $searchDirs += (Join-Path $v.FullName "bin") }
}
foreach ($needed in $info.dlls) {
    $found = $false
    foreach ($dir in $searchDirs) {
        if (Test-Path -LiteralPath (Join-Path $dir $needed)) { $found = $true; $foundDir = $dir; break }
    }
    if ($found) { Write-Host ("  {0}: OK ({1})" -f $needed, $foundDir) }
    else { Write-Host ("  {0}: NOT FOUND  <- CUDA backend cannot initialise" -f $needed) }
}

Write-Host "=== 5. Architecture match ==="
if ($capability) {
    $cap = ($capability -split ',')[1].Trim()
    $sm = "sm_" + ($cap -replace '\.', '')
    if ($info.archs -contains $sm) {
        Write-Host "  GPU is $sm and the backend has native kernels for it: OK"
    } else {
        Write-Host "  GPU is $sm but the backend only has kernels for: $($info.archs -join ', ')"
        Write-Host "  -> no native kernels for this GPU. llama.cpp's own build table requires CUDA >= 12.8 for Blackwell (sm_120)."
    }
} else {
    Write-Host "  compute capability not reported by this driver version"
}

Write-Host "=== 6. What the scorer actually reports ==="
$exe = Join-Path $runtime $Scorer
if (-not (Test-Path -LiteralPath $exe)) { Write-Host "  $Scorer not found in $runtime"; exit 1 }
Write-Host "  running $exe --ngl 999 (reads one empty request, then exits)"
# Feed the scorer an empty stdin so it prints its info line and exits instead of waiting for requests.
$emptyInput = Join-Path $env:TEMP "gpu-probe-empty.txt"
[System.IO.File]::WriteAllText($emptyInput, "")
$process = Start-Process -FilePath $exe -ArgumentList "--model", $Model, "--ngl", "999", "--n-ctx", "4096", "--n-ubatch", "1024" `
    -RedirectStandardInput $emptyInput -RedirectStandardOutput "$runtime\gpu-probe-out.json" `
    -RedirectStandardError "$runtime\gpu-probe-err.txt" -PassThru -NoWindow
$process.WaitForExit(120000)
if (-not $process.HasExited) { $process.Kill() }
$out = Get-Content -LiteralPath "$runtime\gpu-probe-out.json" -Raw -ErrorAction SilentlyContinue
$err = Get-Content -LiteralPath "$runtime\gpu-probe-err.txt" -Raw -ErrorAction SilentlyContinue
Write-Host "  stderr (llama.cpp CUDA errors):"
if ($err) { Write-Host $err } else { Write-Host "    (no warnings -> no CUDA error reported)" }
if ($out) {
    try {
        $json = $out | ConvertFrom-Json
        $gpu = $json.gpu
        Write-Host ("  gpu_backend_present : {0}" -f $gpu.gpu_backend_present)
        Write-Host ("  layers_on_gpu        : {0}" -f $gpu.layers_on_gpu)
        Write-Host ("  requested ngl        : {0}" -f $gpu.requested_ngl)
        foreach ($device in $gpu.gpu_devices) { Write-Host ("  device               : {0} ({1}, {2} MB)" -f $device.name, $device.description, $device.vram_mb) }
        if (-not $gpu.gpu_backend_present) {
            Write-Host "  VERDICT: the CUDA backend library could not be loaded -> CPU inference."
        } elseif ($gpu.layers_on_gpu -eq "cpu") {
            Write-Host "  VERDICT: a GPU backend is registered but 0 layers were offloaded -> CPU inference."
        } else {
            Write-Host "  VERDICT: GPU inference is active ($($gpu.layers_on_gpu) layers on GPU)."
        }
    } catch {
        Write-Host "  could not parse scorer output: $out"
    }
}
Remove-Item -LiteralPath "$runtime\gpu-probe-out.json", "$runtime\gpu-probe-err.txt" -Force -ErrorAction SilentlyContinue
