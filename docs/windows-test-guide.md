# Windows 검증 / 테스트 가이드

이 가이드는 **Windows PC에서 사용자가 직접 수행하는** 검증 절차이다.
macOS 개발 machine에서 Windows EXE/DLL 추론은 실행할 수 없으므로, Windows 관련 항목은
아직 **미검증** 상태다 (see [docs/jev-style-report.md](./jev-style-report.md)).

## 0. Mac에서 준비된 것 / Windows에서 확인해야 할 것

| 항목 | macOS 실측 | Windows 상태 |
|---|---|---|
| 규칙 기반 엔진 + 95 automated tests | PASS (macOS, Python 3.15) | 실행 필요 |
| Jev-Style 0.8B v3 추론 | PASS (macOS: Metal 83 ms/decision, CPU 800 ms/decision; peak RSS 1,648 MB Metal / 2,322 MB CPU @4K) | 미검증 |
| Jev-Style 2B v4 추론 | PASS (macOS: Metal 134 ms/decision, CPU 1,419 ms/decision; peak RSS 2,359 MB Metal / 3,742 MB CPU @4K) | 미검증 |
| 원문 유출 방지 (log/CSV/TXT/AI input/exception) | PASS | 실행 필요 |
| 오프라인 실행 (socket 차단) | PASS | 실행 필요 |
| Windows wheelhouse (win_amd64) | 다운로드·SHA256 기록 완료 | 설치/실행 미검증 |
| `jev-score.exe` / `jev-score-v2.exe` | MinGW cross-build, PE header/import table 확인 | **Windows 실행 미검증** |
| llama.cpp Windows DLL | 공식 b12556 asset (SHA256 기록) | 로딩 미검증 |
| CUDA / RTX 5060 | asset 존재·SHA256 확인 | 미검증 |
| 8GB RAM Windows | macOS 실측 2,319 MB (0.8B CPU) / 3,721 MB (2B CPU) | 미검증 |
| Windows PyInstaller EXE | build 스크립트 준비 완료 | 빌드/실행 미검증 |

## 1. distribution / 준비

GitHub's own "Download ZIP" only contains Git LFS **pointers**, so use one of:

```bash
# A) clone with LFS (needs internet once)
git lfs install
git clone https://github.com/gomin90/piiscan.git
```

```text
# B) GitHub Release assets (each part is below GitHub's 2 GiB asset limit)
#    https://github.com/gomin90/piiscan/releases/tag/v0.3.0
piiscan-app.zip            527.6 MB  186 files  code, runtime, wheels, scripts, docs, manifest
piiscan-model-0.8b.zip     508.6 MB   13 files  0.8B v3 GGUF + tokenizer
piiscan-model-2b.zip      1219.1 MB   13 files  2B v4 GGUF + tokenizer
```

Unzip **all three parts into the same folder** (they share one directory tree), then
run the integrity check in section 2. Built with `python tools/make_release_bundle.py --split`.

- Windows 10/11 x64
- Python 3.12 / 3.13 / 3.14 x64 (EXE 없이 source 실행 시) — `numpy`과 `PyYAML` wheel이 세 태그(cp312/cp313/cp314) 모두 들어 있다
- 8GB RAM 권장 (0.8B model), 16GB+이면 2B model comparison 가능

```powershell
cd C:\piiscan
powershell -ExecutionPolicy Bypass -File scripts\windows\diagnose.ps1
```

`diagnose.ps1` outputs `results\windows\diagnose_<timestamp>.json` and `.txt` with:
Windows version/architecture, CPU, physical/free RAM, GPU + VRAM, NVIDIA driver,
CUDA backend presence, Python version/bits, EXE/DLL presence, model size + SHA256,
scorer integrity, missing required files. No personal or credential data is written.

## 2. 오프라인 번들 무결성

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\verify-offline-bundle.ps1
powershell -ExecutionPolicy Bypass -File scripts\windows\verify-offline-bundle.ps1 -SmokeTest
```

- compares every file against `manifests/offline-bundle-manifest.json` (SHA256)
- exits non-zero on missing file or hash mismatch
- `-SmokeTest` additionally runs `tools\jev_smoke.py` for both models (six synthetic
  columns each) and writes `results\windows\smoke_0.8b-v3.json` / `smoke_2b-v4.json`
- writes `results\windows\bundle_verify_<timestamp>.json`

Expected components:

```
models\jev-style\0.8b-v3\Jev-Style-0.8B-Decision-v3-Q4_K_M.gguf   529,296,864 B
  sha256 0a19bc29bacc33e0d871146c8612b24dd14c2ed2e61cedeb7a928b0852628bac
models\jev-style\2b-v4\Jev-Style-2B-Decision-v4-Q4_K_M.gguf     1,274,396,128 B
  sha256 01a0fd7feb3d456503965ac6058180e8004e3cb71a17907f3f05d80abbe50c5e
runtime\windows\jev-score.exe      (protocol "jev-score",     0.8B v3)
runtime\windows\jev-score-v2.exe   (protocol "jev-score-v2",  2B v4)
runtime\windows\llama.dll, ggml.dll, ggml-base.dll, libomp.dll, ggml-cpu-*.dll
vendor\wheels\*.whl                (31 wheels, ~105 MB)
```

If the ZIP download contains Git LFS *pointer* files instead of the binaries, use
`git clone` (with Git LFS installed) or attach the bundle as a GitHub Release asset.

## 3. 의존성 설치 (offline)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\install-offline.ps1
```

or manually:

```powershell
python -m pip install --no-index --find-links vendor\wheels -r requirements\requirements-win.txt
```

`llama-cpp-python` is **not** in the wheelhouse (no prebuilt win_amd64 wheel); the
legacy generative LocalLLM path therefore needs a source build. The Jev-Style path
does not need it.

## 4. EXE 빌드

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\build-exe.ps1
```

Optional native rebuild of the scorer (recommended for a confirmed Windows binary):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\build-jev-score.ps1
```

Optional NVIDIA backend. Two official llama.cpp b12556 CUDA assets are vendored in
`vendor\llama-windows`:

| asset | size | SHA256 | GPU architectures | needs |
|---|---|---|---|---|
| `llama-b12556-bin-win-cuda-13.5-x64.zip` | 229,295,176 B | `dde6d3f922b2225574ba32fe4bad227fb4784a8a5ebabc78c69fb3da7979ae23` | sm_86, sm_89, sm_103, sm_120, sm_121 | `cublas64_13.dll`, `cublasLt64_13.dll` |
| `llama-b12556-bin-win-cuda-12.4-x64.zip` | 364,747,106 B | `e18df7a7189e1446ca338aa4f04529acb4dce7537164baed2fbc624e48864932` | sm_50, sm_61, sm_70, sm_75, sm_80, sm_90 | `cudart64_12.dll`, `cublas64_12.dll`, `cublasLt64_12.dll` |

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\install-cuda-backend.ps1        # auto-pick from nvidia-smi compute_cap
powershell -ExecutionPolicy Bypass -File scripts\windows\install-cuda-backend.ps1 -CudaVersion 13.5
powershell -ExecutionPolicy Bypass -File scripts\windows\fetch-cuda-backend.ps1 -CudaVersion 13.5   # needs internet once
```

`install-cuda-backend.ps1` verifies the ZIP SHA256, extracts `ggml-cuda.dll` (and the
matching `llama.dll` / `ggml*.dll`, byte-identical to the CPU asset) into
`runtime\windows`, copies `cublas*.dll` from `%CUDA_PATH%\bin` so the CUDA runtime
is resolvable without internet, and finally lists every DLL it placed.

**순서 주의**: `build-exe.ps1` packs the whole `runtime/` directory into
`dist\PrivacyScanner.exe` (`--add-data`). CUDA DLL을 EXE 빌드 ** 후에** 추출하면 packaged EXE에는
CPU backend만 embedded되어 GPU는 never used. After installing the backend either re-run
`build-exe.ps1` or export `GGML_BACKEND_DIR=<bundle>\runtime\windows` before launching.

If the install succeeded but the app still says "ggml-cuda.dll 설치해야 함", read
`hardware.backend_detail.reason` in the report JSON - it names the exact gate:

| reason | fix |
|---|---|
| `no ggml-cuda backend found` | backend not next to the scorer (stale EXE, or run from another directory): re-run `build-exe.ps1` or set `GGML_BACKEND_DIR` |
| `ggml-cuda.dll needs cublas64_13.dll ...` | CUDA toolkit missing: install CUDA 13.3 then re-run `install-cuda-backend.ps1`, or `fetch-cuda-backend.ps1 -CudaVersion 13.5` |
| `GPU is sm_120 but the backend has kernels only for sm_50..sm_90` | wrong asset: `install-cuda-backend.ps1 -CudaVersion 13.5` |

### Why "GPU 있음에도 CPU 추론" happens

Four separate things must all be true. The app now reports each one in the
**하드웨어 정보** tab, in `--report-json` (`hardware.backend_detail`) and in the AI
status line:

1. **Detection** - `nvidia-smi` must be on PATH. If it is not, the probe falls back to
   Windows WMI (`Get-CimInstance Win32_VideoController`), so the card is still listed.
   "GPU detected" therefore only means the card exists, not that CUDA works.
2. **A CUDA backend library must sit in the directory of the scorer that actually runs.**
   `ggml_backend_load_best()` searches only `GGML_BACKEND_DIR` (a **compile-time** macro,
   not set in the official binaries), the **executable's own directory**, then the
   **current working directory**; `GGML_BACKEND_PATH` can add one out-of-tree backend file.
   `PATH` is *not* searched for `ggml-cuda.dll`, so it has to be in `runtime\windows`
   next to `jev-score.exe`. Note that `models\jev-style\<release>\build\*.exe` (built on
   macOS for the bundle) is only usable if the llama.cpp DLLs sit beside it - the manager
   now prefers the `runtime\windows` scorer, whose directory holds them.
3. **The CUDA runtime DLLs that `ggml-cuda.dll` imports must be resolvable.** llama.cpp
   statically links `cudart`, but `cublas`/`cublasLt` stay dynamic and are resolved by the
   Windows loader: exe directory -> System32 -> Windows directory -> CWD -> **PATH**. So
   `cublas64_13.dll` may live next to the scorer, in `%CUDA_PATH%\bin` if that folder is in
   `PATH`, or anywhere on `PATH`. `cuda_backend_status()` reports the exact missing DLL.
4. **The GPU's compute capability must be compiled into that DLL.** The CUDA 12.4 asset
   contains device code only for sm_50-sm_90. Per llama.cpp's own build table, Blackwell
   (sm_120, i.e. RTX 50 series) requires CUDA >= 12.8, so an RTX 5060 cannot run the
   12.4 backend at all - use the 13.5 asset.

Forensics on the shipped files (measured here, not inferred):

```
vendor\llama-windows\llama-b12556-bin-win-cuda-12.4-x64.zip
  ggml-cuda.dll     789,349,376 B
  imports: ggml-base.dll, cudart64_12.dll, cublas64_12.dll, cublasLt64_12.dll, nvcuda.dll
  embedded archs: sm_50, sm_61, sm_70, sm_75, sm_80, sm_90   (PTX ISA 8.4)

vendor\llama-windows\llama-b12556-bin-win-cuda-13.5-x64.zip
  ggml-cuda.dll     220,921,856 B
  imports: ggml-base.dll, cublas64_13.dll, cublasLt64_13.dll
  embedded archs: sm_86, sm_89, sm_103, sm_120, sm_121
```

`runtime\windows` ships **no** `ggml-cuda.dll` at all, so until the install script is
run the GPU is only "detected", never used. `jev-score.exe` and `jev-score-v2.exe`
both import only `llama.dll`, `ggml.dll`, `ggml-base.dll` - GPU support comes entirely
from the dynamically loaded backend, not from the EXE itself.

Run this to get the exact failure on your machine (it prints driver CUDA version, the
DLLs the backend needs, which are missing, the compiled archs vs the GPU's compute
capability, and the llama.cpp stderr when the scorer actually tries to load the GPU):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\gpu-check.ps1
```

`gpu_plan()` refuses to promise GPU layers when any of gates 2-4 fails, and writes the
reason into the AI status line and `hardware.backend_detail`. VRAM smaller than the
model produces a partial offload instead of `--ngl 999`. Use `--gpu-layers 0` (or
`--cpu-only`) to force CPU deterministically.

## 5. 실행 및 결과 수집

```powershell
scripts\windows\run.bat --file samples\windows_sample.csv --debug --report-json results\windows\run_report.json
```

Useful variations:

| 목적 | command |
|---|---|
| 규칙 엔진만 (AI 제외) | `run.bat --no-ai --file samples\windows_sample.csv` |
| 0.8B CPU 추론 | `run.bat --cpu-only --jev-model 0.8b-v3 --file samples\windows_sample.csv` |
| 2B 추론 | `run.bat --jev-model 2b-v4 --file samples\windows_sample.csv` |
| GUI | `run.bat --gui` |
| 모델 없이 EXE 동작 확인 | `run.bat --rule-only` equivalent: `--no-ai` |

`--report-json` writes hardware detection, AI status/backend, load time, peak RSS,
decision counts and rule-vs-AI columns. The report contains masked samples only.

## 6. Hardware matrix to record

For each of the four environments below, run the same two commands and keep the JSON:

1. Windows 8GB RAM, CPU only → `--cpu-only --jev-model 0.8b-v3`
2. Windows 8GB RAM + NVIDIA GPU → `--jev-model 0.8b-v3`
3. Windows RTX 5060 → `--jev-model 0.8b-v3` and `--jev-model 2b-v4`
4. Any Windows PC (fallback check) → `--no-ai`

Record for each: EXE start OK, model load OK, backend, peak RSS (MB), decisions,
failures, skipped reason, wall-clock analysis time.

Peak RSS can be sampled while the run is active:

```powershell
python tools\jev_hardware.py --models 0.8b-v3 2b-v4
```

(that tool is cross-platform; it prints the measured CPU/GPU RSS and timings to
`results/qa/jev_hardware.json`).

## 7. Automation tests on Windows

```powershell
python -m pytest tests -q
python tools\qa_accuracy.py
python tools\jev_offline_check.py
```

`tools/jev_offline_check.py` blocks outbound sockets and confirms the models load and
score purely from local files.

## 8. Known Windows risks (to be confirmed)

- MinGW cross-built scorer may fail to start against the MSVC-built `llama.dll`;
  rebuild with `build-jev-score.ps1` if `jev-score.exe` exits non-zero.
- `llama.dll` requires the Visual C++ runtime (`MSVCP140.dll`, `VCRUNTIME140.dll`);
  a brand-new PC may need the VC++ redistributable.
- ggml selects the CPU backend (`ggml-cpu-<arch>.dll`) at runtime; an unsupported
  older CPU uses `ggml-cpu-x64.dll`.
- CUDA asset is built for CUDA 12.4; RTX 50 (Blackwell) support must be verified
  on the actual GPU.
- 8GB RAM: 2B model (3.7GB peak RSS on macOS CPU) may exceed the limit; the adapter
  aborts AI and continues with rule-based analysis when available RAM is too low.

## 9. Troubleshooting (실제 발생 오류)

| 오류 | 원인 | 해결 |
|---|---|---|
| `ModuleNotFoundError: No module named 'numpy'` | 의존성 설치 단계(§3)를 실행하지 않았거나, Python minor version이 wheelhouse(cp312/313/314)와 다를 | `python --version` 확인 → `powershell -ExecutionPolicy Bypass -File scripts\windows\install-offline.ps1` 재실행. 스크립트가 minor version을 검사하고 missing wheel을 명시적으로 error로 알린다 |
| `No module named 'PySide6'` | same as numpy | `install-offline.ps1` 후 `python -c "import PySide6, numpy, psutil, tokenizers, yaml"` |
| `jev-score.exe` exits non-zero / DLL error | MinGW cross-built binary + MSVC `llama.dll` | `build-jev-score.ps1`로 MSVC native rebuild, or VC++ Redistributable install |
| `pip install` says "no matching distribution" | Python minor 버전 mismatch | cp312 / cp313 / cp314 win_amd64 wheel 중 해당 interpreter를 설치 |
