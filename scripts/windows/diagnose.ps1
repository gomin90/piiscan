# Privacy Scanner - Windows진단 (hardware + bundle inventory)
# Personal information or credentials are not collected. Only system and file metadata.
#
#   powershell -ExecutionPolicy Bypass -File scripts\windows\diagnose.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\windows\diagnose.ps1 -OutputDir results\windows
param(
    [string]$OutputDir = "results\windows",
    [string]$BundleRoot = "."
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path $BundleRoot).Path
$started = Get-Date
$stamp = $started.ToString("yyyyMMdd_HHmmss")

function Get-FileSha256($path) {
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    try { return (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLower() }
    catch { return $null }
}

function Get-FileSize($path) {
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    return (Get-Item -LiteralPath $path).Length
}

$diag = [ordered]@{
    tool             = "piiscan-windows-diagnose/1.0"
    collected_at     = $started.ToString("yyyy-MM-ddTHH:mm:sszzz")
    bundle_root      = $root
    privacy_note     = "System and file metadata only. No CSV content is read."
}

# --- Windows version / architecture -----------------------------------------
# Every CIM query is guarded so the script still produces a report on non-Windows
# hosts (useful for a dry run); on Windows all fields are populated.
$os = $null
$system = $null
try { $os = Get-CimInstance -ClassName Win32_OperatingSystem } catch { Write-Host "warn: Win32_OperatingSystem unavailable" }
try { $system = Get-CimInstance -ClassName Win32_ComputerSystem } catch { }
$diag["windows_version"] = if ($os) { $os.Version } else { "unknown (not Windows)" }
$diag["windows_build"]   = if ($os) { $os.BuildNumber } else { $null }
$diag["windows_edition"] = if ($os) { $os.Caption } else { $null }
$diag["architecture"]    = $env:PROCESSOR_ARCHITECTURE
$diag["is_64bit_os"]     = if ($system) { [bool]$system.Is64Bit } else { $null }
$diag["ps_version"]      = $PSVersionTable.PSVersion.ToString()

# --- CPU --------------------------------------------------------------------
$cpuInfo = [ordered]@{ name = $null; cores = $null; logical = $null; max_clock = $null }
try {
    $cpu = Get-CimInstance -ClassName Win32_Processor
    $cpuInfo["name"]      = $cpu.Name
    $cpuInfo["cores"]     = $cpu.NumberOfCores
    $cpuInfo["logical"]   = $cpu.NumberOfLogicalProcessors
    $cpuInfo["max_clock"] = $cpu.MaxClockSpeed
} catch { }
$diag["cpu"] = $cpuInfo

# --- Memory -----------------------------------------------------------------
$ramInfo = [ordered]@{ total_mb = $null; free_mb = $null; total_gb = $null; headroom_08b = $null; headroom_2b = $null; note = "" }
if ($os) {
    $totalMb = [math]::Round($os.TotalPhysicalMemory / 1024)
    $freeMb  = [math]::Round($os.FreePhysicalMemory / 1024)
    $ramInfo["total_mb"]     = $totalMb
    $ramInfo["free_mb"]      = $freeMb
    $ramInfo["total_gb"]     = [math]::Round($totalMb / 1024.0, 2)
    $ramInfo["headroom_08b"] = $totalMb - 2300 - 512
    $ramInfo["headroom_2b"]  = $totalMb - 3700 - 512
}
$ramInfo["note"] = "macOS-measured peak RSS: 0.8B CPU 2,322 MB, 2B CPU 3,742 MB. 512 MB reserved for OS/Python. Windows values must be re-measured."
$diag["ram"] = $ramInfo

# --- GPU / CUDA ---------------------------------------------------------------
$gpus = @()
try {
    foreach ($g in Get-CimInstance -ClassName Win32_VideoController) {
        if ($g.AdapterMemory) { $vram = [math]::Round($g.AdapterMemory / 1024) } else { $vram = $null }
        $gpuItem = [ordered]@{
            name      = $g.Name
            driver    = $g.DriverVersion
            vram_mb   = $vram
            is_nvidia = ($g.Name -match "NVIDIA")
        }
        $gpus += $gpuItem
    }
} catch { }
$diag["gpus"] = $gpus

$nvsmi = $null
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    try {
        $nvsmi = (& nvidia-smi --query-gpu=name,driver_version,memory.total,memory.free --format=csv,noheader) -join " | "
    } catch {
        $nvsmi = "nvidia-smi exists but failed to run"
    }
}
$diag["nvidia_smi"] = $nvsmi

$cudaDll = Join-Path $root "runtime\windows\ggml-cuda.dll"
$diag["cuda_backend_present"] = Test-Path -LiteralPath $cudaDll
$diag["cuda_note"] = "CPU-only bundle. Run scripts\windows\fetch-cuda-backend.ps1 on a connected machine to add ggml-cuda.dll."

# --- Python -------------------------------------------------------------
$pythonExe = $null
foreach ($candidate in @("python", "py")) {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) { $pythonExe = $candidate; break }
}
if ($pythonExe) {
    $pyVersion = ((& $pythonExe --version) 2>&1 | Out-String).Trim()
    $pythonInfo = [ordered]@{ present = $true; command = $pythonExe; version = $pyVersion }
    $diag["python"] = $pythonInfo
    try {
        $probe = (& $pythonExe -c "import importlib.util; print(','.join(m for m in ['PySide6','psutil','numpy','tokenizers','pyinstaller','pytest'] if importlib.util.find_spec(m)))") 2>&1
        $diag["python_packages_present"] = ($probe | Out-String).Trim()
    } catch {
        $diag["python_packages_present"] = "probe failed"
    }
    try {
        $bits = (& $pythonExe -c "import struct; print(struct.calcsize('P') * 8)") 2>&1
        $diag["python_bits"] = ($bits | Out-String).Trim()
    } catch {
        $diag["python_bits"] = "unknown"
    }
} else {
    $pythonInfo = [ordered]@{ present = $false; note = "Install Python 3.12 x64, or use dist\PrivacyScanner.exe" }
    $diag["python"] = $pythonInfo
}

# --- EXE / DLL / model inventory --------------------------------------------
$exePath = Join-Path $root "dist\PrivacyScanner.exe"
$exeInfo = [ordered]@{ present = (Test-Path -LiteralPath $exePath); path = $exePath }
$exeInfo["size"]  = Get-FileSize $exePath
$exeInfo["sha256"] = Get-FileSha256 $exePath
$diag["app_exe"] = $exeInfo

$runtimeDir = Join-Path $root "runtime\windows"
$missing = @()
foreach ($need in @("jev-score.exe", "jev-score-v2.exe", "llama.dll", "ggml.dll", "ggml-base.dll", "libomp.dll")) {
    if (-not (Test-Path -LiteralPath (Join-Path $runtimeDir $need))) { $missing += $need }
}
$runtimeInfo = [ordered]@{
    directory         = $runtimeDir
    present           = (Test-Path -LiteralPath $runtimeDir)
    jev_score         = Get-FileSha256 (Join-Path $runtimeDir "jev-score.exe")
    jev_score_v2      = Get-FileSha256 (Join-Path $runtimeDir "jev-score-v2.exe")
    llama_dll         = Get-FileSha256 (Join-Path $runtimeDir "llama.dll")
    ggml_dll          = Get-FileSha256 (Join-Path $runtimeDir "ggml.dll")
    ggml_base_dll     = Get-FileSha256 (Join-Path $runtimeDir "ggml-base.dll")
    ggml_cpu_variants = (Get-ChildItem -Path $runtimeDir -Filter "ggml-cpu-*.dll" -ErrorAction SilentlyContinue | Measure-Object).Count
    missing_critical  = $missing
}
$diag["windows_runtime"] = $runtimeInfo

$models = @()
foreach ($release in @("0.8b-v3", "2b-v4")) {
    $dir = Join-Path $root "models\jev-style\$release"
    if (-not (Test-Path -LiteralPath $dir)) { continue }
    $gguf = Get-ChildItem -Path $dir -Filter "*.gguf" -ErrorAction SilentlyContinue | Select-Object -First 1
    $modelItem = [ordered]@{
        release    = $release
        gguf       = $null
        size_bytes = $null
        sha256     = $null
        manifest   = (Test-Path -LiteralPath (Join-Path $dir "manifest.json"))
        tokenizer  = (Test-Path -LiteralPath (Join-Path $dir "tokenizer\tokenizer.json"))
        readout    = (Test-Path -LiteralPath (Join-Path $dir "readout_config.json"))
        runtime_py = (Test-Path -LiteralPath (Join-Path $dir "jev_style_decision_gguf.py"))
    }
    if ($gguf) {
        $modelItem["gguf"] = $gguf.Name
        $modelItem["size_bytes"] = $gguf.Length
        $modelItem["sha256"] = Get-FileSha256 $gguf.FullName
    }
    $models += $modelItem
}
$diag["models"] = $models

$wheelDir = Join-Path $root "vendor\wheels"
$wheels = Get-ChildItem -Path $wheelDir -Filter "*.whl" -ErrorAction SilentlyContinue
$wheelInfo = [ordered]@{ directory = $wheelDir; wheel_count = 0; total_mb = 0 }
if ($wheels) {
    $wheelInfo["wheel_count"] = ($wheels | Measure-Object).Count
    $wheelInfo["total_mb"] = [math]::Round((($wheels | Measure-Object -Sum Length).Sum) / 1MB, 1)
}
$diag["wheelhouse"] = $wheelInfo

$manifestPath = Join-Path $root "manifests\offline-bundle-manifest.json"
$manifestInfo = [ordered]@{ path = $manifestPath; present = (Test-Path -LiteralPath $manifestPath) }
$diag["manifest"] = $manifestInfo

# --- write report ---------------------------------------------------------
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$jsonPath = Join-Path $OutputDir "diagnose_$stamp.json"
$txtPath  = Join-Path $OutputDir "diagnose_$stamp.txt"
$diag | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $jsonPath -Encoding UTF8

$text = New-Object System.Text.StringBuilder
[void]$text.AppendLine("Privacy Scanner Windows diagnosis - $stamp")
[void]$text.AppendLine("Windows: $($diag["windows_version"]) build $($diag["windows_build"]) ($($diag["architecture"]))")
[void]$text.AppendLine("CPU: $($cpuInfo["name"]) cores=$($cpuInfo["cores"]) logical=$($cpuInfo["logical"])")
[void]$text.AppendLine("RAM: total $($ramInfo["total_mb"]) MB, free $($ramInfo["free_mb"]) MB")
foreach ($g in $gpus) {
    [void]$text.AppendLine("GPU: $($g["name"]) VRAM=$($g["vram_mb"]) MB driver=$($g["driver"])")
}
[void]$text.AppendLine("CUDA backend present: $($diag["cuda_backend_present"])")
[void]$text.AppendLine("Python: $pyVersion")
[void]$text.AppendLine("Scorer jev-score.exe    : $($runtimeInfo["jev_score"])")
[void]$text.AppendLine("Scorer jev-score-v2.exe : $($runtimeInfo["jev_score_v2"])")
foreach ($m in $models) {
    [void]$text.AppendLine("Model $($m["release"]): $($m["size_bytes"]) bytes sha256=$($m["sha256"])")
}
if ($missing.Count) { [void]$text.AppendLine("Missing runtime files: $($missing -join ', ')") } else { [void]$text.AppendLine("Missing runtime files: none") }
$text.ToString() | Set-Content -LiteralPath $txtPath -Encoding UTF8

Write-Host $text.ToString()
Write-Host "Saved: $jsonPath, $txtPath"
