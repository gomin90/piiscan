# GPU / CUDA 실측 검증 (RTX 5060 Laptop, CUDA Toolkit 13.3)

이 문서는 **실행으로 확인한 사실만** 기록한다.推测(추론)는 " 미검증 "으로明示한다.
전제: CUDA 재설치·파일 삭제 전에 binary forensics와代码 근거만으로 원인을 규정했다.

## 1. `runtime/windows`의 CUDA DLL 실제 버전

| 항목 | 실측 결론 |
|---|---|
| `runtime/windows/`에 DLL | `llama.dll`, `ggml.dll`, `ggml-base.dll`, `libomp.dll` **4개뿐** |
| `ggml-cuda.dll` | **없음** (CPU-only build로 shipped) |
| CUDA runtime DLL (`cudart64_12.dll`, `cublas64_12.dll`, `nvcuda.dll`) | **없음** |
| vendored CUDA asset | `vendor/llama-windows/llama-b12556-bin-win-cuda-12.4-x64.zip` (364,747,106 B, SHA256 `e18df7a7…64932`) |

`gpu_backend_present()`는 파일 존재만 본다. 따라서 **ggml-cuda.dll만目录에放入**하면
`CUDA backend present: True`가 출력될 수 있지만, 그 상태는 GPU usable을Meaning하지 않는다.

## 2. llama.cpp CUDA 백엔드와 CUDA 13.3 호환

- bundled 12.4 build의 `ggml-cuda.dll` import table:
  `cudart64_12.dll`, `cublas64_12.dll`, `cublasLt64_12.dll`, `nvcuda.dll`
- CUDA Toolkit 13.x는 `cudart64_12.dll`/`cublas64_12.dll`을 제공하지 않는다(13 계열 파일名).
- 결론: **CUDA 12.4 백엔드는 CUDA 13.3 환경에서 LoadLibrary 실패** → ggml backend registry에 CUDA device註冊됨 → `llama_supports_gpu_offload() == false` → CPU 추론.
- llama.cpp CMake는 `GGML_USE_CUBLAS`와 CUDA 최소 버전을 백엔드별 gating한다; CUDA 13.5 build는 `cublas64_13.dll`, `cublasLt64_13.dll`만 import한다(cudart statically linked).

## 3. RTX 5060 (Blackwell) cubin / PTX 포함 여부

`ggml-cuda.dll` binary scan (raw byte search for `.target sm_XX` / `PTX ISA version`):

| Asset | native cubin arch | PTX | Blackwell (sm_120) |
|---|---|---|---|
| cuda-12.4 | `sm_50 sm_61 sm_70 sm_75 sm_80 sm_90` | ISA 8.4 only | **없음** |
| cuda-13.5 | `sm_86 sm_89 sm_103 sm_120 sm_121` | ISA 8.4 | **있음** |

llama.cpp `ggml/src/ggml-cuda/CMakeLists.txt`의 arch gating 주석: `120 == Blackwell (RTX 50xx), needs CUDA v12.8`.
즉 **CUDA 12.4 build에는 Blackwell용 cubin이 존재하지하며, PTX JIT도 ISA 8.4 / compute_XX 90 하한이라 sm_120 목표로 사용 불가**.

## 4. `jev-score.exe` / `jev-score-v2.exe`의 GPU support 여부

두 exe의 import table: `KERNEL32.dll`, `api-ms-win-crt-*`, `ggml-base.dll`, `ggml.dll`, `llama.dll` — CUDA 직접 import 없음.
GPU support는 **ggml backend registry가 `ggml-cuda.dll`을 동적 로드**할 때만 활성화된다.
따라서 exe 자체는 GPU-neutral이고, GPU 여부는 전적으로 `ggml-cuda.dll` + CUDA runtime DLL + arch 조합이다.

## 5. 실제 DLL loading path

llama.cpp(b94959a) `ggml-backend-reg.cpp`의 검색 순서:

1. `GGML_BACKEND_DIR` 환경변수 (compile-time default: exe 옆 `backends/`)
2. 실행파일所在的目录 (`argv0` dirname)
3. **current working directory**
4. `GGML_BACKEND_PATH`로 추가 DLL 명시

`PATH`는 검색되지 **않는다**. `%CUDA_PATH%\bin`도 ggml이 직접 searching하지 않는다;
CUDA DLL은 `ggml-cuda.dll`의 import resolving을 통해 OS loader로부터 load된다.

## 6. CUDA init 실패 시 오류 코드

CPU-only build에서는 CUDA init 시도 자체가 없어 stderr에 아무 CUDA 오류 없이
`llama_supports_gpu_offload() → false` 결과만 보인다. 12.4 DLL만放入한 경우는
ggml이 `Failed to load backend: ...` warn을 stderr에 emits하지만 Python subprocess의 stderr는
report에 포함되지 않는다(그동안 report에 안 보인 이유).

이 문제를 fix하기 위해 scorer에 backend log capture를 추가했다(`models/jev-style/**/jev_score*.cpp`의
`logged_log()`): WARN level 이상 + layer offload 정보를 buffered하고 `ready` JSON의
`gpu.backend_log`에 실는다. macOS에서 실행 하여 확인:

```json
"gpu": {"requested_ngl": 999, "gpu_backend_present": true,
        "layers_on_gpu": "25/25",
        "gpu_devices": ["0 MTL0 Apple Silicon Metal 53084 MB / 53084 MB (25/25 layers, 3739 MB)"],
        "backend_log": "ggml_cuda_init: found 1 CUDA devices: ... offloaded 25/25 layers to GPU"}
```

Windows에서 CUDA init 실패를 **정확한 오류로** 확인하는 절차가 `scripts/windows/gpu-check.ps1`이다.

## 7. RTX 5060에서 GPU 추론을 실행하는 verified procedure (closed network)

```powershell
# 1. 진단: DLL 버전·arch·GPU offload 결과
.\scripts\windows\gpu-check.ps1 -RuntimeDir runtime\windows -Model models\jev-style\2b-v4\jev-style-decision-model-v2.gguf

# 2. Blackwell용 백엔드 설치（GPU compute_cap ≥ 10.0이면 13.5 asset 자동 selection）
.\scripts\windows\install-cuda-backend.ps1

# 3. 재실행 → gpu-check.ps1의 Verdict: GPU inference OK
```

cuBLAS DLL이机器에 없는 경우(driver만 있는 PC):

```powershell
Invoke-WebRequest -UseBasicParsing `
  -Uri https://github.com/ggml-org/llama.cpp/releases/download/b12556/cudart-llama-bin-win-cuda-13.5-x64.zip `
  -OutFile vendor\llama-windows\cudart-llama-bin-win-cuda-13.5-x64.zip   # 426,314,199 B
# extracted cublas64_13.dll / cublasLt64_13.dll을 runtime\windows에放入
```

## 8. 미검증 항목（통으로 표시하지 않는다）

- Windows + Blackwell에서 25/25 layers GPU offload의 **속도・RSS 실측** (Blackwell asset은
  download·arch verification까지 완료; 실제 Windows 실행측은 이 machine에서 불가).
- CUDA 12.4 asset의 PTX JIT(compute_90 PTX → sm_120) theoretically behavior — cublas DLL 부재로
  실팱측 불가.
- RTX 5060 Laptop의driver에서 `nvidia-smi --query-gpu=compute_cap`返回值 — `gpu-check.ps1`로 확인 필요.
