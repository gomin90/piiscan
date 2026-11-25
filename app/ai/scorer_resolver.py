"""One place that decides *which* scorer binary and *which* ggml backend are used.

Spec section 8: model_manager, gpu_detector and the Windows PowerShell scripts
all resolve the scorer, and they must agree. The rules encoded here are:

1. an explicit ``JEV_SCORE_BIN`` / ``JEV_SCORE_V2_BIN`` environment variable wins;
2. otherwise the model's own ``build/`` directory is searched;
3. on Windows the shipped ``runtime/windows`` scorer is the next candidate;
4. a backend library (``ggml-cuda.dll`` / ``libggml-metal.dylib`` / ``ggml-cuda.so``)
   is only honoured when it sits in ``GGML_BACKEND_DIR``, ``GGML_BACKEND_PATH``,
   the scorer's own directory or the current working directory - the directories
   llama.cpp actually searches. Finding a library **only** on ``PATH`` does *not*
   mean the backend will be loaded, and it is reported as such.
5. a CPU run is never described as GPU success: the resolver returns the selected
   scorer, the backend library path, the GPU model, the compute capability, the
   requested ``ngl`` and, when GPU inference is not active, an explicit
   ``fallback_reason``.

Nothing here downloads anything; every path is a local file.
"""
from __future__ import annotations

import os
import platform
from pathlib import Path

WINDOWS = os.name == "nt"

#: Environment variable per scorer binary name.
SCORER_ENV = {"jev-score": "JEV_SCORE_BIN", "jev-score-v2": "JEV_SCORE_V2_BIN"}

#: Backend library names llama.cpp will dynamically load, per platform.
BACKEND_NAMES = {
    "windows": ("ggml-cuda.dll", "ggml-cpu.dll", "ggml-cpu-*.dll"),
    "macos": ("libggml-metal.dylib", "libggml-cuda.dylib", "libggml-cpu.dylib"),
    "linux": ("ggml-cuda.so", "libggml-cuda.so", "ggml-cpu.so"),
}


def _system_key(system: str | None = None) -> str:
    return {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}.get(system or platform.system(), "")


def _env_dirs() -> list[Path]:
    out: list[Path] = []
    for name in ("GGML_BACKEND_DIR", "GGML_BACKEND_PATH"):
        value = os.environ.get(name, "")
        for entry in value.split(os.pathsep):
            if entry:
                out.append(Path(entry))
    return out


def scorer_candidates(model_dir: Path, scorer_bin: str, project_root: Path,
                      windows: bool | None = None) -> list[Path]:
    """Every scorer location the resolver considers, in precedence order.

    `windows` lets a caller that already knows its target platform (for example the
    model manager, which can be run for a packaged Windows bundle) override the
    platform of the machine this code happens to be executing on.
    """
    is_windows = WINDOWS if windows is None else windows
    candidates: list[Path] = []
    env_bin = os.environ.get(SCORER_ENV.get(scorer_bin, ""), "")
    if env_bin:
        candidates.append(Path(env_bin))
    if is_windows:
        candidates.append(model_dir / "build" / f"{scorer_bin}.exe")
        candidates.append(project_root / "runtime" / "windows" / f"{scorer_bin}.exe")
    else:
        candidates.append(model_dir / "build" / scorer_bin)
        candidates.append(project_root / "runtime" / "macos" / scorer_bin)
    return candidates


def select_scorer(model_dir: Path, scorer_bin: str, project_root: Path,
                  windows: bool | None = None) -> tuple[Path | None, list[str]]:
    """Return (chosen scorer path, notes explaining why the others were rejected)."""
    notes: list[str] = []
    for candidate in scorer_candidates(model_dir, scorer_bin, project_root, windows):
        if candidate.is_file():
            return candidate, notes
        notes.append(f"not found: {candidate}")
    return None, [f"no {scorer_bin} binary in any of: " + ", ".join(
        str(c) for c in scorer_candidates(model_dir, scorer_bin, project_root))]


def _project_backend_dirs() -> list[Path]:
    """Shipped backend locations in the offline bundle (runtime/ and model build/)."""
    root = Path(__file__).resolve().parents[2]
    dirs = [root / "runtime" / name for name in ("windows", "macos", "linux")]
    models = root / "models" / "jev-style"
    if models.is_dir():
        dirs.extend(release / "build" for release in models.iterdir() if release.is_dir())
    return [d for d in dirs if d.is_dir()]


def backend_search_dirs(scorer_path: Path | None) -> list[Path]:
    """Directories llama.cpp searches for a dynamically loaded ggml backend.

    llama.cpp itself looks in GGML_BACKEND_DIR / GGML_BACKEND_PATH, the directory of
    the executable and the CWD. When no scorer path is known (a plain hardware probe)
    the bundle's own runtime and model build directories are searched as well, since
    that is where a shipped backend actually sits.
    """
    dirs = _env_dirs()
    if scorer_path:
        candidate = Path(scorer_path)
        # Callers may hand over either the scorer binary or the directory that
        # holds it; both must work.
        dirs.append(candidate if candidate.is_dir() else candidate.parent)
    else:
        dirs.extend(_project_backend_dirs())
    dirs.append(Path.cwd())
    return [d for d in dirs if d and d.exists()]


def path_only_dirs() -> list[Path]:
    """PATH entries - reported, but never treated as a loaded backend."""
    return [Path(entry) for entry in os.environ.get("PATH", "").split(os.pathsep) if entry]


def find_backend(scorer_path: Path | None, system: str | None = None) -> dict:
    """Locate a ggml backend library. `found_via_path` is deliberately reported
    separately so a library that exists on PATH is never claimed as loaded."""
    names = BACKEND_NAMES.get(_system_key(system), ())
    searched = backend_search_dirs(scorer_path)
    for name in names:
        if "*" in name:
            for directory in searched:
                matches = sorted(directory.glob(name))
                if matches:
                    return {"found": True, "path": str(matches[0]), "name": matches[0].name,
                            "source": str(matches[0].parent), "found_via_path": False}
            continue
        for directory in searched:
            candidate = directory / name
            if candidate.exists():
                return {"found": True, "path": str(candidate), "name": name,
                        "source": str(directory), "found_via_path": False}

    for directory in path_only_dirs():
        for name in names:
            candidate = directory / name
            if candidate.exists():
                return {"found": False, "path": str(candidate), "name": name,
                        "source": str(directory), "found_via_path": True,
                        "reason": (f"{name} exists on PATH but not in a directory llama.cpp "
                                   "searches (GGML_BACKEND_DIR / scorer directory / CWD) "
                                   "-> it will not be loaded")}
    return {"found": False, "path": "", "name": "", "source": "", "found_via_path": False,
            "reason": "no ggml backend library in GGML_BACKEND_DIR, the scorer directory or the CWD"}


def inference_report(scorer_path: Path | None, gpu_model: str = "", compute_capability: str = "",
                     requested_ngl: int = 0, layers_on_gpu: str | int = "cpu",
                     backend: str = "cpu") -> dict:
    """The shape every report (GUI, JSON, PowerShell) uses for "which scorer ran".

    `layers_on_gpu == "cpu"` or `0` together with `backend == "cpu"` is a CPU run;
    callers must not describe it as GPU inference.
    """
    backend_info = find_backend(scorer_path)
    return {
        "selected_scorer_path": str(scorer_path) if scorer_path else None,
        "backend_library_path": backend_info["path"] or None,
        "backend_found_via_path_only": backend_info.get("found_via_path", False),
        "gpu_model": gpu_model or None,
        "compute_capability": compute_capability or None,
        "requested_ngl": requested_ngl,
        "layers_on_gpu": layers_on_gpu,
        "backend": backend,
        "gpu_active": backend != "cpu" and layers_on_gpu not in ("cpu", 0, "0"),
        "fallback_reason": None if backend != "cpu" else (
            backend_info.get("reason") or "GPU backend not loaded -> CPU inference"
        ),
    }
