# GPU / CUDA 실측 검증 (RTX 5060 Laptop, CUDA Toolkit 13.3)

이 문서는 binary·code로 확인한 사실만 기록한다. 추론은 "미검증"으로 구분한다.
CUDA 재설치나 파일 삭제 없이, shipped binary의 forensics와 vendored code 근거만으로
원인을 규정했다.

## 1. `runtime/windows`의 CUDA DLL 실제 버전

| 항목 | 실측 결과 |
|---|---|
| `runtime/windows/` DLL | `llama.dll`, `ggml.dll`, `ggml-base.dll`, `libomp.dll` 4개뿐 |
| `ggml-cuda.dll` | 없음 (CPU-only asset에서 추출) |
| CUDA runtime DLL (`cudart64_12.dll`, `cublas64_12.dll`, `cublasLt64_12.dll`, `nvcuda.dll`) | 없음 |
| vendored CUDA asset | `llama-b12556-bin-win-cuda-12.4-x64.zip` (364,747,106 B, SHA256 `e18df7a7...64932`), `llama-b12556-bin-win-cuda-13.5-x64.zip` (229,295,176 B, SHA256 `dde6d3f9...ae23`) |

`gpu_backend_present()`는 파일 존재만 본다. `ggml-cuda.dll`을 `runtime\windows`에 넣으면
`CUDA backend present: True`가 printed될 수 있지만, 그 상태는 GPU usable을 의미하지 않는다.

## 2. llama.cpp CUDA backend과 CUDA 13.3 호환

| asset | required CUDA runtime DLL | compiled archs |
|---|---|---|
| cuda-12.4 | `cudart64_12.dll`, `cublas64_12.dll`, `cublasLt64_12.dll`, `nvcuda.dll` | sm_50, sm_61, sm_70, sm_75, sm_80, sm_90 (PTX ISA 8.4) |
| cuda-13.5 | `cublas64_13.dll`, `cublasLt64_13.dll` (cudart statically linked) | sm_86, sm_89, sm_103, sm_120, sm_121 |

CUDA 12.4 backend은 CUDA 13 계열 환경에서 `cublas64_12.dll`이 resolve되지하면 LoadLibrary 실패,
ggml backend registry에 CUDA device가등록 않고 `llama_supports_gpu_offload() == false` → CPU fallback.

## 3. Blackwell (RTX 5060) cubin / PTX 포함 여부

- Blackwell compute capability = **sm_120**.
- bundled 12.4 asset의 arch scan: sm_50 ×144, sm_61 ×204, sm_70 ×122, sm_75 ×84, sm_80 ×113,
  sm_90 ×269. **sm_120 없음**.
- detector-side detail: `app/ai/gpu_detector.py` keeps the kernel list as bare numbers
  (`120`) and normalises the device arch (`sm_120`) before comparing, so a backend that
  does carry `sm_120` kernels is reported as a match. Scanning both vendored assets found
  **no `compute_XX` PTX strings**, so the detector never claims PTX JIT for them.
- llama.cpp `ggml/src/ggml-cuda/CMakeLists.txt`의 comment: `120 == Blackwell (RTX 50xx), needs CUDA
  v12.8` → CUDA 12.8+ build 없으면 Blackwell kernels 생성되지 않음.
- 따라서 `ggml-cuda.dll`(12.4)를 placement해도 driver만 있는 PC에서 cublas 12 DLL resolve 실패로
  CUDA init 실패 → GPU 없음으로 판정 → CPU 추론. 실제 증상과 일치.

## 4. `jev-score.exe` / `jev-score-v2.exe`의 GPU support

두 exe의 PE imports: `KERNEL32.dll`, `api-ms-win-crt-runtime`, `ggml.dll`, `llama.dll`,
`ggml-base.dll`. CUDA 직접 import 없음. `llama.dll`(3,955,200 B)은 CPU/CUDA asset에서
byte-identical → GPU support는 exe에 정적 링크된 것이 아니라 `ggml-cuda.dll`이 exe 옆에서
동적으로 load될 때만 활성화된다.

## 5. 실제 DLL loading 경로

llama.cpp `ggml/src/ggml-backend-reg.cpp`의 search order:

```text
GGML_BACKEND_DIR (env, default exe-adjacent) -> exe directory -> CWD -> GGML_BACKEND_PATH
```

`PATH`는 search되지 않는다. `ggml-cuda.dll`은 exe와 같은 폴더에 있거나 `GGML_BACKEND_PATH`로
explicitly해야 한다. CUDA runtime DLL은 `ggml-cuda.dll`의 imports가 OS loader에 의해 resolve될 때
find된다.

## 6. CUDA 초기화 실패 시 exact error 코드

CPU-only build에서는 CUDA init 시도 자체가 없어 stderr에 CUDA error 없이
`llama_supports_gpu_offload() -> false`만 보인다. backend load 실패 warn은 stderr로 emit되지만
report에 포함되지 않았다(이전 버전).

Scorer에 `logged_log()`로 backend log capture를 추가(WARN 이상 + offload lines buffer)하고
`ready` JSON의 `gpu` field에 담는다. macOS(Metal) 실측:

```json
"gpu": {"requested_ngl": 999, "gpu_backend_present": true,
        "layers_on_gpu": "25/25",
        "gpu_devices": ["0 MTL0 Apple Silicon Metal 53084 MB / 53084 MB (25/25 layers, 3739 MB)"],
        "backend_log": "ggml_backend_reg: ... MTL0 Apple Silicon Metal 53084 MB / 53084 MB (25/25 layers, 3739 MB) offloaded 25/25 layers to GPU"}
```

Windows에서 exact error·DLL·arch 확인 절차:

```powershell
.\scripts\windows\gpu-check.ps1 -RuntimeDir runtime\windows `
  -Model models\jev-style\2b-v4\jev-style-decision-model-v2.gguf
```

출력: GPU/driver/compute_cap, runtime DLL inventory, `ggml-cuda.dll` linked CUDA DLL·archs,
resolve 가능 여부, arch match, live scorer의 `requested_ngl / gpu_backend_present / layers_on_gpu /
backend_log` verdict.

## 7. RTX 5060에서 GPU inference 실행 procedure (closed network)

```powershell
# 1 diagnose
.\scripts\windows\gpu-check.ps1 -RuntimeDir runtime\windows -Model models\jev-style\2b-v4\jev-style-decision-model-v2.gguf
# 2 install Blackwell backend (compute_cap >= 10.0 -> CUDA 13.5 asset 자동 선택)
.\scripts\windows\install-cuda-backend.ps1
# 3 re-run gpu-check.ps1 -> Verdict: GPU inference OK
```

cuBLAS DLL이 시스템에 없는 경우(driver만 installed):

```powershell
Invoke-WebRequest -UseBasicParsing `
  -Uri https://github.com/ggml-org/llama.cpp/releases/download/b12556/cudart-llama-bin-win-cuda-13.5-x64.zip `
  -OutFile vendor/llama-windows/cudart-llama-bin-win-cuda-13.5-x64.zip   # 426,314,199 B
```

extracted `cublas64_13.dll`, `cublasLt64_13.dll`을 exe 옆에 두어 재실행.

## 8. 미 검증 항목 (pass로 표시하지 않음)

- Blackwell + CUDA 13.5 asset의 Windows throughput·RSS 실측 (macOS Metal 실측 완료; Windows
  Blackwell 실행은 이 machine에서 불가).
- CUDA 12.4 asset의 PTX JIT(`compute_90` PTX → sm_120) 동작: cublas DLL 부재로 실행 검증 불가.
- RTX 5060 Laptop driver의 `nvidia-smi --query-gpu=compute_cap` return값 — `gpu-check.ps1`로 확인.
