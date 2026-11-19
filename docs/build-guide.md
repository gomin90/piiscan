# Build guide

## Prerequisites

Builds must be produced on the target operating system. A macOS binary cannot
run on Windows and vice versa.

- Python 3.10+
- PyInstaller
- PySide6
- llama-cpp-python (built with CUDA on the Windows/RTX host)
- GGUF model placed in `models/` with its SHA256 in `models/model_manifest.json`

## Commands

```
python tools/build.py --target macos
python tools/build.py --target windows
python tools/build.py --target windows --debug
python tools/build.py --target macos --verify-only
```

`--debug` enables the diagnostics logger (encoding detection, batch timings,
LLM load/inference time, token counts). Debug mode is a build-time switch, not
a runtime menu.

## What gets bundled

- `app/` source
- `models/` GGUF files (SHA256 verified before the build)
- `resources/rules` and `resources/prompts`
- native llama.cpp library (CPU build on macOS, CUDA build on Windows)

PyInstaller one-file executables extract their payload to a temp directory at
runtime. The app checks that the temp directory is writable before loading the
model and, if it is not, falls back to the rule-only pipeline.

## GPU note

`app/ai/gpu_detector.py` probes `nvidia-smi` for model, total/free VRAM and the
driver version. RTX 5060 (8 GB) runs the 1.5B Q4 model with full offload when
free VRAM is sufficient, otherwise partial offload, otherwise CPU. A GPU error
never aborts the program: it is reported in Korean in the UI and the CPU path is
used instead.
