"""하드웨어 탐지: GPU/CUDA availability, RAM, CPU, backend 선택.

GPU is only *usable* when a GPU-capable llama.cpp backend is actually present next to
the scorer binary. A detected NVIDIA card without ggml-cuda.dll therefore falls back to
CPU with an explicit note, so the report never claims a GPU acceleration that is not real.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

GPU_BACKENDS = {
    "windows": ("ggml-cuda.dll", "ggml-cuda.dylib"),
    "linux": ("libggml-cuda.so", "ggml-cuda.so"),
    "macos": ("libggml-metal.dylib", "ggml-metal.dylib"),
}


@dataclass
class HardwareReport:
    os_name: str
    cpu_model: str
    cpu_count: int
    total_ram_gb: float
    available_ram_gb: float
    gpu_vendor: str
    gpu_model: str
    vram_total_gb: float
    vram_free_gb: float
    cuda_runtime: str
    backend: str  # cpu | cuda | metal
    gpu_backend_ready: bool = False  # a GPU-capable llama.cpp backend is actually installed
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "os": self.os_name,
            "cpu": self.cpu_model,
            "cpu_count": self.cpu_count,
            "ram_total_gb": round(self.total_ram_gb, 2),
            "ram_available_gb": round(self.available_ram_gb, 2),
            "gpu": f"{self.gpu_vendor} {self.gpu_model}".strip(),
            "vram_total_gb": round(self.vram_total_gb, 2),
            "vram_free_gb": round(self.vram_free_gb, 2),
            "cuda": self.cuda_runtime,
            "backend": self.backend,
            "gpu_backend_ready": self.gpu_backend_ready,
            "notes": self.notes,
        }


def _free_memory_gb() -> float:
    try:
        import psutil

        return psutil.virtual_memory().available / (1024 ** 3)
    except ImportError:
        pass
    try:
        with open("/proc/meminfo") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1024 / 1024
    except OSError:
        pass
    total = _total_memory_gb()
    return max(total * 0.5, 0.5)


def _total_memory_gb() -> float:
    try:
        import psutil

        return psutil.virtual_memory().total / (1024 ** 3)
    except ImportError:
        pass
    try:
        with open("/proc/meminfo") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) / 1024 / 1024
    except OSError:
        pass
    return 8.0


def _system_key(system: str | None = None) -> str:
    return {"Windows": "windows", "Linux": "linux", "Darwin": "macos"}.get(system or platform.system(), "")


def _search_dirs(binary_dir: Path | None) -> list[Path]:
    root = Path(__file__).resolve().parents[2]
    dirs = []
    if binary_dir:
        dirs.append(Path(binary_dir))
    dirs += [
        root / "runtime" / "windows",
        root / "runtime" / "macos",
        root / "runtime" / "linux",
        root / "models" / "jev-style" / "0.8b-v3" / "build",
        root / "models" / "jev-style" / "2b-v4" / "build",
    ]
    return [d for d in dirs if d and d.exists()]


def gpu_backend_present(binary_dir: Path | None = None, system: str | None = None) -> tuple[bool, str]:
    """Is a GPU-capable llama.cpp backend actually installed next to the scorer?

    Returns (found, path). Detecting an NVIDIA card is not enough: without ggml-cuda.dll
    (Windows) or libggml-metal.dylib (macOS) llama.cpp runs on the CPU.
    """
    key = {"Windows": "windows", "Linux": "linux", "Darwin": "macos"}.get(system or platform.system(), "")
    for name in GPU_BACKENDS.get(key, ()):
        for directory in _search_dirs(binary_dir):
            candidate = directory / name
            if candidate.exists():
                return True, str(candidate)
    return False, ""


def _nvidia_gpu() -> tuple[str, str, float, float, str]:
    """Return (vendor, model, vram_total, vram_free, driver) using nvidia-smi or PowerShell."""
    if not shutil.which("nvidia-smi"):
        return "", "", 0.0, 0.0, ""
    query = "gpu_name,memory.total,memory.free,driver_version"
    try:
        output = subprocess.run(
            ["nvidia-smi", f"--query={query}", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=6,
        )
    except (subprocess.TimeoutExpired, OSError):
        return "", "", 0.0, 0.0, ""
    if output.returncode != 0:
        return "", "", 0.0, 0.0, ""
    parts = [p.strip() for p in output.stdout.strip().splitlines()[0].split(",")]
    if len(parts) < 4:
        return "", "", 0.0, 0.0, ""
    model = parts[0]
    total = float(parts[1]) / 1024
    free = float(parts[2]) / 1024
    driver = parts[3]
    return "NVIDIA", model, total, free, driver


def _gpu_via_powershell() -> tuple[str, str, float, float, str]:
    """Windows fallback: enumerate display adapters through WMI when nvidia-smi is absent."""
    if platform.system() != "Windows":
        return "", "", 0.0, 0.0, ""
    script = (
        "Get-CimInstance -ClassName Win32_VideoController | "
        "ForEach-Object { $_.Name + '|' + $_.AdapterMemory + '|' + $_.DriverVersion }"
    )
    try:
        output = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError):
        return "", "", 0.0, 0.0, ""
    if output.returncode != 0:
        return "", "", 0.0, 0.0, ""
    for line in output.stdout.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2 or not parts[0]:
            continue
        name = parts[0]
        try:
            total = float(parts[1]) / (1024 ** 3)
        except (ValueError, IndexError):
            total = 0.0
        driver = parts[2] if len(parts) > 2 else ""
        vendor = "NVIDIA" if "nvidia" in name.lower() or "geforce" in name.lower() or "rtx" in name.lower() else (
            "AMD" if "radeon" in name.lower() else "Other"
        )
        return vendor, name, total, total, driver
    return "", "", 0.0, 0.0, ""


def gpu_plan(scorer_path: Path, hardware: HardwareReport, requested_layers: int | None,
             model_bytes: int) -> tuple[str, int, str]:
    """Decide (backend, n_gpu_layers, note) for a scorer binary.

    A GPU that has no ggml backend library next to the scorer cannot be used, so the
    plan falls back to CPU and explains why. VRAM smaller than the model triggers a
    partial offload instead of a full one.
    """
    backend_ready, _ = gpu_backend_present(Path(scorer_path).parent,
                                          getattr(hardware, "os_name", None))
    hw_backend = getattr(hardware, "backend", "cpu")

    if requested_layers is not None:
        layers = max(0, int(requested_layers))
        if layers and not backend_ready:
            return "cpu", 0, (
                f"--gpu-layers {layers} requested but no ggml GPU backend library is installed "
                "next to the scorer -> CPU inference"
            )
        return (hw_backend if layers else "cpu"), layers, ""

    if hw_backend in ("cuda", "metal") and backend_ready:
        layers = 999
        vram = hardware.vram_free_gb or hardware.vram_total_gb
        model_gb = model_bytes / (1024 ** 3)
        if vram and model_gb > vram:
            layers = max(1, int(999 * max(vram / model_gb, 0.1)))
            return hw_backend, layers, (
                f"VRAM {vram:.1f}GB < model {model_gb:.1f}GB -> partial offload ({layers} layers)"
            )
        return hw_backend, layers, ""

    if hw_backend in ("cuda", "metal"):
        return "cpu", 0, (
            f"{hw_backend} GPU detected but no ggml GPU backend library sits next to the scorer "
            "(ggml-cuda.dll / libggml-metal.dylib) -> CPU inference"
        )
    return "cpu", 0, ""


def detect_hardware(binary_dir: Path | None = None, system: str | None = None) -> HardwareReport:
    """Probe the machine and decide which inference backend can really be used."""
    system = system or platform.system()
    vendor, model, vram_total, vram_free, cuda = _nvidia_gpu()
    if not vendor and system == "Windows":
        vendor, model, vram_total, vram_free, cuda = _gpu_via_powershell()
    notes: list[str] = []

    gpu_found = bool(vendor)
    backend_ready, backend_path = gpu_backend_present(binary_dir, system)

    if vendor == "NVIDIA":
        if backend_ready:
            backend = "cuda"
            notes.append(f"CUDA backend 사용 가능: {backend_path}")
        else:
            backend = "cpu"
            notes.append(
                f"NVIDIA {model} 감지(VRAM {vram_total:.1f}GB): ggml-cuda.dll 없음 -> CPU 추론. "
                "GPU 사용려면 scripts/windows/install-cuda-backend.ps1 실행 후 scorer rebuild(-GPU)"
            )
    elif vendor and vendor != "NVIDIA":
        backend = "cpu"
        notes.append(
            f"{vendor} {model} 감지: 이 오프라인 번들에는 NVIDIA CUDA backend만 제공 -> CPU 추론 "
            "(AMD/ROCm backend 없음)."
        )
    elif system == "Darwin":
        if backend_ready:
            backend = "metal"
            notes.append(f"Apple Silicon Metal backend: {backend_path}")
        else:
            backend = "cpu"
            notes.append("macOS: libggml-metal.dylib 없음 -> CPU 추론")
    else:
        backend = "cpu"
        notes.append("GPU 없음 -> CPU 추론")

    if backend == "cuda" and vram_free < 2.0:
        notes.append("VRAM 부족 -> partial offload 또는 CPU fallback 예정")

    return HardwareReport(
        os_name=system,
        cpu_model=platform.processor() or platform.machine(),
        cpu_count=platform.os.cpu_count() or 1,
        total_ram_gb=_total_memory_gb(),
        available_ram_gb=_free_memory_gb(),
        gpu_vendor=vendor,
        gpu_model=model,
        vram_total_gb=vram_total,
        vram_free_gb=vram_free,
        cuda_runtime=cuda,
        backend=backend,
        gpu_backend_ready=backend_ready,
        notes=notes,
    )
