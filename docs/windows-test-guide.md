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
- Python 3.12 x64 (EXE 없이 source 실행 시) — 3.11 also works, but the wheelhouse is pinned for 3.12
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

Optional NVIDIA backend. The official llama.cpp CUDA 12.4 asset is **already inside
the bundle**, so no download is needed:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\install-cuda-backend.ps1   # offline, from vendor\llama-windows
# or, if you want a fresh copy from the llama.cpp release page:
powershell -ExecutionPolicy Bypass -File scripts\windows\fetch-cuda-backend.ps1     # needs internet once
```

`install-cuda-backend.ps1` verifies the ZIP SHA256 before extracting `ggml-cuda.dll`
into `runtime\windows`. Whether the GPU is then actually used must come from a real run.

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
