"""하드웨어 탐지: GPU/CUDA availability, RAM, CPU, backend 선택.

GPU is only *usable* when three things are all true:
  1. an NVIDIA/Apple GPU is present,
  2. the matching ggml backend library (ggml-cuda.dll / libggml-metal.dylib) sits next to
     the scorer binary, and
  3. that backend can actually initialise on this machine: its CUDA runtime DLLs resolve
     and the GPU's compute capability was compiled into the backend (or can be JIT-ed).

Any missing link falls back to CPU with an explicit note, so the report never claims a GPU
acceleration that is not real.
"""
from __future__ import annotations

import ctypes
import os
import platform
import re
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
    gpu_compute_arch: str = ""       # GPU compute capability, e.g. sm_120 for an RTX 5060
    backend_detail: dict = field(default_factory=dict)  # why the backend is (un)usable
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
            "gpu_compute_arch": self.gpu_compute_arch,
            "backend_detail": self.backend_detail,
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
    """Directories llama.cpp can load a ggml backend from, in its own search order.

    ggml_backend_reg.cpp searches GGML_BACKEND_DIR, the executable's directory, the
    current working directory, then GGML_BACKEND_PATH. PATH is *not* searched, so a
    backend that is neither next to the scorer nor in one of the bundle runtime dirs is
    invisible - which is why this list, not the CWD alone, decides the verdict.
    """
    root = Path(__file__).resolve().parents[2]
    dirs = []
    for env_var in ("GGML_BACKEND_DIR", "GGML_BACKEND_PATH"):
        for entry in os.environ.get(env_var, "").split(os.pathsep):
            if entry:
                dirs.append(Path(entry))
    if binary_dir:
        dirs.append(Path(binary_dir))
    dirs += [
        root / "runtime" / "windows",
        root / "runtime" / "macos",
        root / "runtime" / "linux",
        Path.cwd(),
        root / "runtime",
        root / "models" / "jev-style" / "0.8b-v3" / "build",
        root / "models" / "jev-style" / "2b-v4" / "build",
    ]
    return [d for d in dirs if d and d.exists()]


CUDA_BACKEND_NAMES = ("ggml-cuda.dll", "libggml-cuda.so", "libggml-cuda.dylib")


def find_gpu_backend(binary_dir: Path | None = None, system: str | None = None,
                    names: tuple[str, ...] | None = None) -> tuple[str, Path | None]:
    """Locate the GPU backend library this machine could actually load.

    Returns (library name, path). Every gate in the detector uses this single lookup so
    "backend present" and "backend usable" can never disagree about where the library is.
    """
    if names is None:
        key = _system_key(system)
        names = GPU_BACKENDS.get(key, ()) or tuple(name for group in GPU_BACKENDS.values() for name in group)
    for name in names:
        for directory in _search_dirs(binary_dir):
            candidate = directory / name
            if candidate.is_file():
                return name, candidate
    return "", None


def gpu_backend_present(binary_dir: Path | None = None, system: str | None = None) -> tuple[bool, str]:
    """Is a GPU-capable llama.cpp backend actually installed next to the scorer?

    Returns (found, path). Detecting an NVIDIA card is not enough: without ggml-cuda.dll
    (Windows) or libggml-metal.dylib (macOS) llama.cpp runs on the CPU.
    """
    name, path = find_gpu_backend(binary_dir, system)
    return (path is not None), str(path) if path else ""


# ---------------------------------------------------------------- CUDA forensics
# ggml loads its backends dynamically: ggml-base.dll looks for ggml-<name>.dll in the
# directory of the *executable* and the current working directory. A ggml-cuda.dll that
# cannot be loaded (missing CUDA runtime DLLs, unsupported GPU arch) is skipped silently
# except for a llama.cpp warning on stderr, and inference continues on the CPU.

_CUDA_DLL_PATTERN = re.compile(rb"(?:cudart|cublasLt|cublas)64_\d+\.dll")


def _linked_cuda_dlls(path: Path) -> list[str]:
    """CUDA runtime DLL names referenced by a backend binary (its import table).

    Scanning the raw bytes is robust and dependency-free; the names appear verbatim in
    the PE import section, e.g. cudart64_12.dll in the llama.cpp CUDA 12.4 build.
    """
    found: list[str] = []
    try:
        with path.open("rb") as handle:
            tail = b""
            while True:
                block = handle.read(1 << 22)
                if not block:
                    break
                chunk = tail + block
                tail = chunk[-16:]
                for match in _CUDA_DLL_PATTERN.finditer(chunk):
                    name = match.group(0).decode("utf-8", "replace")
                    if name not in found:
                        found.append(name)
    except OSError:
        return []
    return found


def _cuda_dll_search_dirs(binary_dir: Path | None) -> list[Path]:
    """Directories Windows would resolve a CUDA runtime DLL from when loading ggml-cuda.dll."""
    dirs = []
    if binary_dir:
        dirs.append(Path(binary_dir))
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if entry:
            dirs.append(Path(entry))
    toolkit_root = Path(os.environ.get("CUDA_PATH", r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA"))
    # CUDA 13.x on Windows puts the runtime DLLs in bin\x64; CUDA 11/12 keep them in bin.
    # Both are checked so a 12.4-era toolkit and a 13.3-era toolkit are both resolved.
    if toolkit_root.exists():
        for candidate in (toolkit_root / "bin", toolkit_root / "bin" / "x64"):
            dirs.append(candidate)
        for child in toolkit_root.iterdir():
            if child.is_dir():
                dirs.append(child / "bin")
                dirs.append(child / "bin" / "x64")
    return [d for d in dirs if d and d.is_dir()]


def _arch_number(value: str) -> str:
    """Normalise an architecture label to its bare digits.

    'sm_120', '120' and '12.0' all mean compute capability 12.0. Backend kernels are
    read out of the DLL as 'sm_120' and the device is reported as 'sm_120', so every
    comparison in this module must go through this normalisation - comparing
    'sm_120' against the stored bare '120' silently reports a kernel mismatch for a
    GPU the backend actually supports.
    """
    return re.sub(r"[^0-9]", "", value or "")


def _compiled_cuda_archs(path: Path) -> tuple[list[str], list[str]]:
    """Device code (sm_XX) and PTX (compute_XX) numbers embedded in a ggml-cuda backend.

    Both lists are bare numbers ("120", not "sm_120"), sorted numerically.
    """
    device_archs: set[str] = set()
    ptx_archs: set[str] = set()
    try:
        with path.open("rb") as handle:
            chunk = b""
            while True:
                block = handle.read(1 << 22)
                if not block:
                    break
                chunk = chunk[-16:] + block
                device_archs.update(match.group(1).decode() for match in re.finditer(rb"sm_(\d{2,3})", chunk))
                ptx_archs.update(match.group(1).decode() for match in re.finditer(rb"compute_(\d{2,3})", chunk))
    except OSError:
        return [], []
    return sorted(device_archs, key=int), sorted(ptx_archs, key=int)


def _device_arch_from_smi() -> str:
    """Compute capability of the first NVIDIA GPU straight from nvidia-smi (e.g. 12.0 -> sm_120)."""
    if not shutil.which("nvidia-smi"):
        return ""
    try:
        output = subprocess.run(["nvidia-smi", "--query-gpu=compute_cap", "--format=csv,noheader"],
                                capture_output=True, text=True, timeout=6)
    except (subprocess.TimeoutExpired, OSError):
        return ""
    for line in output.stdout.splitlines():
        cap = line.strip().replace(".", "")
        if cap.isdigit():
            return "sm_" + cap
    return ""


def _cuda_driver_version() -> str:
    """CUDA version the installed NVIDIA driver exposes (from nvidia-smi header)."""
    if not shutil.which("nvidia-smi"):
        return ""
    try:
        output = subprocess.run(["nvidia-smi"], capture_output=True, text=True, timeout=6)
    except (subprocess.TimeoutExpired, OSError):
        return ""
    match = re.search(r"CUDA Version:\s*([0-9.]+)", output.stdout)
    return match.group(1) if match else ""


def cuda_device_arch() -> str:
    """Compute capability of the first CUDA device, e.g. 'sm_120' for an RTX 5060.

    Uses the CUDA runtime directly (ctypes), so it works without llama.cpp installed.
    Returns "" when no CUDA runtime can be loaded or no device is present.
    """
    for name in ("cudart64_13.dll", "cudart64_12.dll", "cudart64_110.dll", "cudart.dll", "libcudart.so", "libcudart.so.12"):
        try:
            runtime = ctypes.CDLL(name)
            runtime.cudaInitialize()
        except OSError:
            continue
        try:
            count = ctypes.c_int(0)
            if runtime.cudaDeviceGetCount(ctypes.byref(count)) != 0 or count.value <= 0:
                continue
            major = ctypes.c_int(0)
            minor = ctypes.c_int(0)
            # CU_DEVICE_ATTRIBUTE_COMPUTE_CAPABILITY_MAJOR = 17, MINOR = 18
            if runtime.cudaDeviceGetAttribute(ctypes.byref(major), 17, 0) != 0:
                continue
            runtime.cudaDeviceGetAttribute(ctypes.byref(minor), 18, 0)
            return f"sm_{major.value}{minor.value}"
        except Exception:
            continue
    return ""


def analyze_cuda_backend(library: Path, binary_dir: Path | None = None,
                         device_arch: str | None = None) -> dict:
    """Diagnose one CUDA backend library: runtime imports, compiled archs, GPU match.

    A CUDA backend is usable only when three independent gates pass:
      1. the ggml-cuda.dll library is installed where llama.cpp looks for it
         (next to the scorer, the CWD, or GGML_BACKEND_PATH);
      2. every CUDA runtime DLL it imports resolves on this machine;
      3. it contains native device code for the GPU's compute capability
         (or PTX the driver can JIT).
    Reporting "CPU inference" without naming the failing gate is what made the
    RTX 5060 / CUDA 13.3 case look like a silently ignored GPU.
    """
    status = {
        "present": True,
        "library": str(library),
        "runtime_dlls": [],
        "missing": [],
        "archs": [],
        "ptx_archs": [],
        "device_arch": "",
        "driver_cuda": "",
        "usable": False,
        "reason": "",
    }
    required = _linked_cuda_dlls(Path(library))
    status["runtime_dlls"] = required
    search_dirs = [Path(binary_dir)] if binary_dir else []
    search_dirs += _cuda_dll_search_dirs(binary_dir)
    status["missing"] = [name for name in required
                         if not any((directory / name).is_file() for directory in search_dirs)]
    status["archs"], status["ptx_archs"] = _compiled_cuda_archs(Path(library))
    status["device_arch"] = device_arch if device_arch is not None else (
        cuda_device_arch() or _device_arch_from_smi()
    )
    status["driver_cuda"] = _cuda_driver_version()

    device_number = _arch_number(status["device_arch"])

    if status["missing"]:
        majors = sorted({name.split("_")[1].split(".")[0] for name in status["missing"]})
        status["reason"] = (
            "ggml-cuda.dll needs " + ", ".join(status["missing"]) + " (CUDA " + "/".join(majors) + " runtime), "
            "which is not installed here -> llama.cpp cannot load the CUDA backend and runs on CPU"
        )
    elif not device_number or not status["archs"]:
        status["usable"] = True
        status["reason"] = "library and CUDA runtime DLLs resolve; GPU compute capability not queryable"
    elif device_number in status["archs"]:
        status["usable"] = True
        status["reason"] = f"library, CUDA runtime and native kernels for {status['device_arch']} all match"
    else:
        # PTX is forward compatible: a driver can JIT compute_90 PTX on an sm_120 GPU,
        # so a backend without a native kernel for this GPU can still run.
        jit_targets = [a for a in status["ptx_archs"] if int(a) <= int(device_number)]
        native = ", ".join("sm_" + a for a in status["archs"])
        if jit_targets:
            status["usable"] = True
            status["reason"] = (
                f"no native kernels for {status['device_arch']} (native kernels for {native}); "
                f"the driver JIT-compiles compute_{jit_targets[-1]} PTX instead"
            )
        else:
            status["reason"] = (
                f"GPU is {status['device_arch']} but the backend has native kernels only for "
                + native + "; no kernels or JIT-able PTX for this GPU"
            )
    return status


def cuda_backend_status(binary_dir: Path | None = None) -> dict:
    """Locate the CUDA backend in a runtime directory and diagnose it."""
    _, library = find_gpu_backend(binary_dir, names=CUDA_BACKEND_NAMES)
    if library is None:
        searched = ", ".join(str(d) for d in _search_dirs(binary_dir)) or "no runtime directory"
        return {
            "present": False, "library": "", "runtime_dlls": [], "missing": [], "archs": [],
            "ptx_archs": [], "device_arch": "", "driver_cuda": "", "usable": False,
            "reason": f"no ggml-cuda backend found (searched {searched}) -> llama.cpp must run on CPU",
        }
    return analyze_cuda_backend(library, library.parent)


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
    scorer_dir = Path(scorer_path).parent
    backend_ready, _ = gpu_backend_present(scorer_dir, getattr(hardware, "os_name", None))
    hw_backend = getattr(hardware, "backend", "cpu")

    # A GPU backend library that cannot load (wrong CUDA runtime, no kernels for the GPU
    # architecture) is a CPU run in disguise: check it before promising GPU layers.
    if hw_backend == "cuda" and backend_ready and requested_layers != 0:
        cuda = cuda_backend_status(scorer_dir)
        if not cuda["usable"]:
            return "cpu", 0, cuda["reason"]

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


def _cuda_fix_hint(detail: dict) -> str:
    """Name the exact next command for whichever CUDA gate failed."""
    if not detail.get("present"):
        return (
            "scripts\\windows\\install-cuda-backend.ps1 실행 (vendor\\llama-windows의 ggml-cuda.dll을 "
            "runtime\\windows에 추출). EXE를 이미 빌드했다면 DLL이 runtime\\windows에 들어온 뒤 "
            "build-exe.ps1로 다시빌드하거나 GGML_BACKEND_DIR=runtime\\windows로 지정해야 함 "
            "(llama.cpp는 PATH를 search하지 않는다)."
        )
    if detail.get("missing"):
        return (
            f"{', '.join(detail['missing'])} 없음: CUDA toolkit(13.3 또는 12.4) 설치 후 "
            "install-cuda-backend.ps1 재실행(%CUDA_PATH%의 bin 또는 bin\\x64에서 cublas DLL을 복사한다), 또는 "
            "scripts\\windows\\fetch-cuda-backend.ps1 -CudaVersion 13.5로 runtime DLL 확보."
        )
    return (
        "GPU compute capability와 backend kernel이 mismatch: "
        "install-cuda-backend.ps1 -CudaVersion 13.5 (sm_86/sm_89/sm_103/sm_120/sm_121; "
        "the 12.4 asset only carries sm_50-sm_90 kernel)."
    )


def detect_hardware(binary_dir: Path | None = None, system: str | None = None) -> HardwareReport:
    """Probe the machine and decide which inference backend can really be used."""
    system = system or platform.system()
    vendor, model, vram_total, vram_free, cuda = _nvidia_gpu()
    if not vendor and system == "Windows":
        vendor, model, vram_total, vram_free, cuda = _gpu_via_powershell()
    notes: list[str] = []
    gpu_arch = ""
    backend_detail: dict = {}

    gpu_found = bool(vendor)
    backend_ready, backend_path = gpu_backend_present(binary_dir, system)

    if vendor == "NVIDIA":
        detail = cuda_backend_status(binary_dir)
        arch = detail.get("device_arch", "") or cuda_device_arch()
        if detail.get("usable"):
            backend = "cuda"
            notes.append(f"CUDA backend 사용 가능: {detail['library']} ({arch or 'compute capability 알 수 없음'})")
        else:
            backend = "cpu"
            notes.append(
                f"NVIDIA {model} 감지(VRAM {vram_total:.1f}GB) but CUDA cannot be used: {detail['reason']}. "
                f"조치: {_cuda_fix_hint(detail)}"
            )
        detail["gpu_model"] = model
        detail["gpu_vendor"] = vendor
        gpu_arch = arch
        backend_detail = detail
    elif vendor and vendor != "NVIDIA":
        backend = "cpu"
        notes.append(
            f"{vendor} {model} 감지: 이 오프라인 번들에는 NVIDIA CUDA backend만 제공 -> CPU 추론 "
            "(AMD/ROCm backend 없음)."
        )
    elif system == "Darwin":
        if backend_ready:
            backend = "metal"
            vendor, model = "Apple Silicon", "Metal"
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
        gpu_compute_arch=gpu_arch,
        backend_detail=backend_detail,
        notes=notes,
    )
