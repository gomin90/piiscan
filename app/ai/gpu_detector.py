"""하드웨어 탐지: GPU/CUDA availability, RAM, CPU, backend 선택."""
from __future__ import annotations

import platform
import shutil
import subprocess
from dataclasses import dataclass, field


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


def _nvidia_gpu() -> tuple[str, str, float, float, str]:
    """Return (vendor, model, vram_total, vram_free, cuda_runtime) via nvidia-smi."""
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


def detect_hardware() -> HardwareReport:
    system = platform.system()
    vendor, model, vram_total, vram_free, cuda = _nvidia_gpu()
    notes: list[str] = []
    if vendor == "NVIDIA":
        backend = "cuda"
        if vram_free < 2.0:
            notes.append("VRAM 부족 -> CPU 병행 또는 CPU fallback 예정")
    elif system == "Darwin":
        backend = "metal"
        notes.append("Apple Silicon: Metal backend 선택 가능 (기본 CPU 우선 검증)")
    else:
        backend = "cpu"
        notes.append("GPU 없음 -> CPU 추론")
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
        notes=notes,
    )
