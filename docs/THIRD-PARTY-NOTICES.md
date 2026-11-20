# Third-party notices

This document lists every third-party component shipped in the offline bundle and
the licence under which it is distributed. The repository code is MIT-licensed
(see `LICENSE`); the model artifacts keep their upstream licence.

## AI models

| Component | Source | Licence | Where |
|---|---|---|---|
| Jev-Style-0.8B-Decision-v3 (Q4_K_M GGUF) | `chaoliangUNSW/Jev-Style-0.8B-Decision-v3-GGUF` | Apache-2.0 | `models/jev-style/0.8b-v3/LICENSE`, `NOTICE` |
| Jev-Style-2B-Decision-v4 (Q4_K_M GGUF) | `chaoliangUNSW/Jev-Style-2B-Decision-v4-GGUF` | Apache-2.0 | `models/jev-style/2b-v4/LICENSE`, `NOTICE` |
| Official scorer sources (`jev_score.cpp`, `jev_score_v2.cpp`) and GGUF runtime (`jev_style_decision_gguf.py`) | same repositories | Apache-2.0 | inside each model directory |

## Native libraries

| Component | Version | Licence | Notes |
|---|---|---|---|
| llama.cpp / ggml (`llama.dll`, `ggml.dll`, `ggml-base.dll`, `ggml-cpu-*.dll`, `libomp.dll`) | b12556 (commit `b94959a`) | MIT | official Windows x64 CPU asset; LLVM OpenMP runtime inside is Apache-2.0 with LLVM exception |
| `jev-score.exe`, `jev-score-v2.exe` | built from the Apache-2.0 scorer sources | Apache-2.0 | cross-built on macOS with MinGW-w64 14.0.0; statically linked; **not executed on Windows yet** |
| GCC/MinGW runtime that is statically linked into the scorer | 14.0.0 | GCC Runtime Library Exception (linking exception) | no runtime DLLs required; provenance recorded in `runtime/windows/BUILD-INFO.json` |

## Python wheels in `vendor/wheels/` (win_amd64)

| Package | Version | Licence |
|---|---|---|
| PySide6_Essentials, shiboken6 | 6.12.0 | LGPL-3.0 (Qt) / MIT (shiboken) |
| pyinstaller, pyinstaller-hooks-contrib | 6.22.3 / 2026.10 | GPL-3.0+ (PyInstaller) / MIT (hooks) |
| altgraph, packaging, pluggy, iniconfig | 0.17.5 / 26.3 / 1.7.0 / 2.3.1 | MIT |
| psutil | 7.2.2 | BSD-3-Clause |
| numpy | 2.5.5 | BSD-3-Clause |
| tokenizers | 0.23.3 | Apache-2.0 |
| huggingface_hub, hf-xet | 2.4.0 / 1.8.0 | Apache-2.0 |
| filelock, fsspec, pygments | 4.2.12 / 2026.9.0 / 2.21.0 | BSD-3-Clause (filelock BSD-3), BSD-3-Clause |
| click, idna, tqdm, pyyaml, setuptools, anyio, h11, httpcore2, httpx2, truststore, typing_extensions | as pinned | BSD-3-Clause / MIT / Apache-2.0 per package |

The exact `LICENSE` file for every wheel is inside the wheel itself
(`*.dist-info/LICENSE.dist`). `requirements/requirements-lock.txt` records each
wheel's SHA256 so a distribution can be integrity-checked offline.

## Not shipped

- `llama-cpp-python` (no published win_amd64 wheel; the legacy generative path is
  optional and untested on Windows).
- PySide6 Addons / WebEngine / Pdf (unused by this application).
