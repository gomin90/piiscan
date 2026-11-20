# Offline (air-gapped) distribution

Everything needed for a closed-network deployment is prepared in advance. No
install script touches the internet, and the application never downloads a model
at run time.

## Bundle contents

```
app/ resources/ tests/            rule engine, detectors, GUI, synthetic fixtures only
models/jev-style/0.8b-v3/         GGUF Q4_K_M, tokenizer/, readout_config.json,
                                  manifest.json, jev_style_decision_gguf.py,
                                  jev_score.cpp (scorer source)
models/jev-style/2b-v4/           same structure with jev_score_v2.cpp
runtime/windows/                  jev-score.exe, jev-score-v2.exe, llama.dll, ggml.dll,
                                  ggml-base.dll, libomp.dll, ggml-cpu-<arch>.dll (14),
                                  BUILD-INFO.json (provenance + SHA256)
vendor/wheels/                    31 win_amd64 wheels, 105 MB (Python 3.12 x64)
requirements/                     requirements.txt, requirements-win.txt,
                                  requirements-lock.txt (name, size, SHA256)
manifests/                        offline-bundle-manifest.json (SHA256 of every file)
scripts/windows/                  diagnose / verify / install / build / run
docs/                             user guide, architecture, this file,
                                  windows-test-guide.md, THIRD-PARTY-NOTICES.md
samples/                          synthetic CSV for smoke tests
```

Large binaries (GGUF, wheels, DLLs, EXEs) are tracked with Git LFS:

```
git lfs install
git clone https://github.com/gomin90/piiscan.git
```

`git clone` materialises the LFS objects. GitHub's "Download ZIP" of a repository
that uses LFS can deliver pointer files instead of binaries, so for a
closed-network hand-over either clone with LFS or build the archive locally and
attach it to a GitHub Release:

```
python tools/make_bundle_manifest.py                 # regenerate the manifest
python tools/make_release_bundle.py                  # dist/piiscan-offline-bundle.zip
python tools/make_release_bundle.py --rule-only      # no GGUF models
python tools/make_release_bundle.py --no-wheels      # code + models only
```

## Preparing the bundle on the development machine (macOS)

```
python tools/jev_download_models.py                  # Hugging Face -> models/jev-style/...
python tools/make_bundle_manifest.py                 # manifests/offline-bundle-manifest.json
python tools/verify_wheelhouse.py                    # dependency closure check
python -m pytest tests -q                            # 95 tests
```

The Windows scorer is cross-built with MinGW against the official llama.cpp
Windows DLLs and shipped in `runtime/windows/`:

```
x86_64-w64-mingw32-g++ -std=c++17 -O2 -static \
    -I vendor/llama.cpp/include -I vendor/llama.cpp/ggml/include \
    -I vendor/llama.cpp/vendor \
    models/jev-style/0.8b-v3/jev_score.cpp \
    -o runtime/windows/jev-score.exe \
    runtime/windows/llama.dll runtime/windows/ggml.dll runtime/windows/ggml-base.dll
```

Inputs (llama.cpp b12556 win-cpu-x64 assets) and every output hash are recorded in
`runtime/windows/BUILD-INFO.json`. **Those EXEs have never been executed on
Windows** - they are cross-compile artefacts, so the Windows verification step is
mandatory before any deployment claim.

## Steps on a Windows machine

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\diagnose.ps1
powershell -ExecutionPolicy Bypass -File scripts\windows\verify-offline-bundle.ps1
powershell -ExecutionPolicy Bypass -File scripts\windows\install-offline.ps1
scripts\windows\run.bat --file samples\windows_sample.csv --debug --report-json results\windows\report.json
```

Only the machine that actually runs the EXE can confirm DLL loading, inference and
memory usage. Full procedure: [docs/windows-test-guide.md](./windows-test-guide.md).

## Integrity

- Every dependency is pinned in `requirements/requirements-lock.txt` with wheel
  name, byte size and SHA256.
- `models/jev-style/<release>/manifest.json` records the Hugging Face repository,
  revision, quantization, licence, size and SHA256 of each file;
  `JevStyleModelManager` refuses a GGUF whose hash does not match
  (`app/ai/jev/model_manager.py`).
- `manifests/offline-bundle-manifest.json` covers all distributable files and
  `scripts/windows/verify-offline-bundle.ps1` re-hashes them on the target machine
  (`--verify` does the same on the dev machine).
- On Windows the native libraries next to the scorer are checked before a model is
  considered usable: `llama.dll`, `ggml.dll`, `ggml-base.dll`, `libomp.dll` and at
  least one `ggml-cpu-*.dll`.
- No network access is used in the analysis path: there is no API client, and the
  GGUF, tokenizer and scorer are read from local files. Downloads happen only
  through `tools/jev_download_models.py` on a connected dev machine.
- `tools/jev_offline_check.py` and `tests/qa/test_offline_check.py` confirm the
  analysis runs with all outbound sockets blocked.
