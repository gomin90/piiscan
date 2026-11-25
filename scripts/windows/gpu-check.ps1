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
$gpu = @()
try {
    $gpu = Get-CimInstance -ClassName Win32_VideoController -ErrorAction Stop
} catch {
    Write-Host "  Get-CimInstance unavailable - this probe needs Windows (PowerShell 5.1+)."
}
foreach ($g in $gpu) {
    $vramMb = if ($g.AdapterMemory) { [int]($g.AdapterMemory / 1MB) } else { 0 }
    Write-Host ("  {0} | driver {1} | VRAM {2} MB" -f $g.Name, $g.DriverVersion, $vramMb)
}
$capability = ""
$smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if ($smi) {
    try {
        $header = ((nvidia-smi 2>$null | Select-Object -First 6) -join "`n")
        $cudaVersion = if ($header -match 'CUDA Version:\s*([0-9.]+)') { $Matches[1] } else { "unknown" }
        # --query returns one line per GPU; keep the first so -split ',' below is defined.
        $capLines = @(nvidia-smi --query-gpu=name,compute_cap --format=csv,noheader 2>$null)
        $capability = if ($capLines.Count -ge 1) { $capLines[0] } else { "" }
        Write-Host "  nvidia-smi CUDA version: $cudaVersion"
        if ($capability) { Write-Host "  compute capability: $capability" }
        $driverCuda = if ($header -match 'Driver Version:\s*([0-9.]+)') { $Matches[1] } else { "" }
        Write-Host "  driver: $driverCuda"
    } catch {
        Write-Host "  nvidia-smi ran but failed: $_"
    }
} else {
    Write-Host "  nvidia-smi not found -> NVIDIA driver is likely not installed"
}

Write-Host "=== 2. CUDA toolkit present on the machine ==="
$cudaRoot = if ($env:CUDA_PATH) { $env:CUDA_PATH } else { "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA" }
$toolkits = New-Object System.Collections.Generic.List[string]
if (Test-Path -LiteralPath $cudaRoot) {
    foreach ($v in (Get-ChildItem -LiteralPath $cudaRoot -Directory)) {
        if ($v.Name -match '^v?(\d+\.\d+)') { $toolkits.Add($Matches[1]) }
    }
}
if ($nvcc = Get-Command nvcc -ErrorAction SilentlyContinue) {
    try {
        $nv = ((nvcc --version) -join " ")
        if ($nv -match 'release (\d+\.\d+)') { $toolkits.Add($Matches[1]) }
    } catch {
        Write-Host "  nvcc found but 'nvcc --version' failed: $_"
    }
}
if ($toolkits) {
    Write-Host "  installed CUDA toolkits: $($toolkits | Sort-Object -Unique -Descending | Select-Object -First 5)"
    if ($toolkits -contains "13.3") {
        Write-Host "  CUDA 13.3 toolkit found. install-cuda-backend.ps1 uses the CUDA 13.5 prebuilt backend, which"
        Write-Host "  runs on the same CUDA 13 runtime DLLs; scripts\windows\build-cuda-133-backend.ps1 compiles the exact 13.3 build."
    }
} else {
    Write-Host "  no CUDA toolkit detected. The CUDA 13 backend can still run on the NVIDIA driver's"
    Write-Host "  redistributable DLLs if they are on PATH or copied into $runtime."
}

Write-Host "=== 3. Backend DLLs installed in $runtime ==="
$dlls = Get-ChildItem -LiteralPath $runtime -Filter *.dll | Select-Object Name, @{n='MB';e={[math]::Round($_.Length/1MB,1)}}
foreach ($d in $dlls) { Write-Host ("  {0,-28} {1,8} MB" -f $d.Name, $d.MB) }
$cudaPath = Join-Path $runtime "ggml-cuda.dll"
if (-not (Test-Path -LiteralPath $cudaPath)) {
    Write-Host "  ggml-cuda.dll MISSING -> llama.cpp has no CUDA backend, so inference is CPU-only."
    Write-Host "  Fix: scripts\windows\install-cuda-backend.ps1"
    exit 0
}

Write-Host "=== 4. What ggml-cuda.dll needs ==="
$info = Read-CudaStrings $cudaPath
Write-Host ("  CUDA runtime DLLs imported: {0}" -f ($info.dlls -join ", "))
Write-Host ("  GPU architectures compiled in: {0}" -f ($info.archs -join ", "))

Write-Host "=== 5. Can those DLLs be found? ==="
# The scorer loads ggml-cuda.dll from its own directory, so both the runtime folder and
# the model's build folder are places the DLLs have to be reachable from.
$searchDirs = @($runtime, (Join-Path (Split-Path -LiteralPath $Model) "build"))
foreach ($entry in ($env:PATH -split ';')) { if ($entry) { $searchDirs += $entry } }
$cudaRoot = if ($env:CUDA_PATH) { $env:CUDA_PATH } else { "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA" }
if (Test-Path -LiteralPath $cudaRoot) {
    foreach ($v in (Get-ChildItem -LiteralPath $cudaRoot -Directory)) {
        $searchDirs += (Join-Path $v.FullName "bin")
        # CUDA 13.x puts cudart/cublas in bin\x64.
        $searchDirs += (Join-Path $v.FullName "bin\x64")
    }
}
foreach ($needed in $info.dlls) {
    $found = $false
    $foundDir = ""
    foreach ($dir in $searchDirs) {
        try {
            if ($dir -and (Test-Path -LiteralPath (Join-Path $dir $needed))) {
                $found = $true
                $foundDir = $dir
                break
            }
        } catch {
            # A malformed PATH entry is not worth stopping the whole probe.
        }
    }
    if ($found) { Write-Host ("  {0}: OK ({1})" -f $needed, $foundDir) }
    else { Write-Host ("  {0}: NOT FOUND  <- CUDA backend cannot initialise" -f $needed) }
}

Write-Host "=== 6. Architecture match ==="
$capParts = $capability -split ','
if ($capParts.Count -ge 2) {
    $cap = $capParts[1].Trim()
    $sm = "sm_" + ($cap -replace '\.', '')
    if ($info.archs -contains $sm) {
        Write-Host "  GPU is $sm and the backend has native kernels for it: OK"
    } else {
        Write-Host "  GPU is $sm but the backend only has kernels for: $($info.archs -join ', ')"
        Write-Host "  -> no native kernels for this GPU. llama.cpp's own build table requires CUDA >= 12.8 for Blackwell (sm_120)."
        Write-Host "  Fix: scripts\windows\install-cuda-backend.ps1 -CudaVersion 13.5"
    }
} else {
    Write-Host "  compute capability not reported by this driver version (nvidia-smi returned: '$capability')"
}

Write-Host "=== 7. What the scorer actually reports (the only real success test) ==="
$exe = Join-Path $runtime $Scorer
if (-not (Test-Path -LiteralPath $exe)) { Write-Host "  $Scorer not found in $runtime"; exit 1 }
if (-not (Test-Path -LiteralPath $Model)) {
    Write-Host "  model file not found: $Model"
    Write-Host "  pass -Model models\jev-style\2b-v4\Jev-Style-2B-Decision-v4-Q4_K_M.gguf"
    exit 1
}
Write-Host "  running $exe --ngl 999 (reads one empty request, then exits)"
# Feed the scorer an empty stdin so it prints its info line and exits instead of waiting for requests.
# Keep the probe files out of runtime\ so they never end up packed into the EXE.
$probeDir = Join-Path $env:TEMP "piiscan-gpu-probe"
New-Item -ItemType Directory -Path $probeDir -Force | Out-Null
$emptyInput = Join-Path $probeDir "empty-request.txt"
[System.IO.File]::WriteAllText($emptyInput, "")
$process = Start-Process -FilePath $exe -ArgumentList "--model", $Model, "--ngl", "999", "--n-ctx", "4096", "--n-ubatch", "1024" `
    -RedirectStandardInput $emptyInput -RedirectStandardOutput (Join-Path $probeDir "out.json") `
    -RedirectStandardError (Join-Path $probeDir "err.txt") -PassThru -WindowStyle Hidden
$process.WaitForExit(120000)
if (-not $process.HasExited) { $process.Kill() }
$out = Get-Content -LiteralPath (Join-Path $probeDir "out.json") -Raw -ErrorAction SilentlyContinue
$err = Get-Content -LiteralPath (Join-Path $probeDir "err.txt") -Raw -ErrorAction SilentlyContinue
Write-Host "  stderr (llama.cpp CUDA errors):"
if ($err) { Write-Host $err } else { Write-Host "    (no warnings -> no CUDA error reported)" }
if ($out) {
    try {
        $json = $out | ConvertFrom-Json
        $gpu = $json.gpu
        if (-not $gpu) {
            Write-Host "  the scorer did not report a gpu section: $out"
        } else {
            Write-Host ("  gpu_backend_present : {0}" -f $gpu.gpu_backend_present)
            Write-Host ("  layers_on_gpu        : {0}" -f $gpu.layers_on_gpu)
            Write-Host ("  requested ngl        : {0}" -f $gpu.requested_ngl)
            foreach ($device in ($gpu.gpu_devices | Where-Object { $_ })) {
                Write-Host ("  device               : {0} ({1}, {2} MB)" -f $device.name, $device.description, $device.vram_mb)
            }
            if (-not $gpu.gpu_backend_present) {
                Write-Host "  VERDICT: the CUDA backend library could not be loaded -> CPU inference."
            } elseif ($gpu.layers_on_gpu -eq "cpu") {
                Write-Host "  VERDICT: a GPU backend is registered but 0 layers were offloaded -> CPU inference."
            } else {
                Write-Host "  VERDICT: GPU inference is active ($($gpu.layers_on_gpu) layers on GPU)."
            }
        }
    } catch {
        Write-Host "  could not parse scorer output: $out"
    }
}

Write-Host "=== 8. Next step ==="
if (-not (Test-Path -LiteralPath $cudaPath)) {
    Write-Host "  install the backend: scripts\windows\install-cuda-backend.ps1"
    Write-Host "  then re-run this probe, and rebuild the EXE (build-exe.ps1) so it contains the new DLLs"
} else {
    Write-Host "  re-run this probe after any change; the last section is the verdict that matters."
}
Write-Host "  full diagnostics: scripts\windows\diagnose.ps1"
Remove-Item -LiteralPath $probeDir -Recurse -Force -ErrorAction SilentlyContinue
