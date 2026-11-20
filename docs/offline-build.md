# Offline (air-gapped) build

Everything needed for a closed-network build is prepared in advance; no install
script touches the internet.

## Bundle contents

```
vendor/wheels/          PySide6, PyInstaller, llama-cpp-python, psutil wheels
                      (must be copied in from a connected machine; the repository
                       does not ship the wheels - verify with `ls vendor/wheels` before step 1)
models/jev-style/       Jev-Style decision models + runtimes
  <release>/            GGUF (Q4_K_M), tokenizer/, readout_config.json,
                        release_config.json, manifest.json, package_manifest.json,
                        jev_style_decision_gguf.py, build/jev-score[-v2] + dylibs
resources/              prompt and rule files
tools/                  build.py, benchmark.py
tests/                  synthetic fixtures only, no real personal data
```

The scorer binaries are made self-contained before packaging: run
`python tools/jev_package.py` once on the build machine. It copies the llama.cpp
shared libraries into `<release>/build/` and rewrites the rpath to `@loader_path`
so the bundle works when copied to a machine without `vendor/llama.cpp`.

## Steps on the air-gapped machine

1. `python -m pip install --no-index --find-links vendor/wheels -r requirements/requirements-lock.txt`
2. `python tools/build.py --target windows --verify-only`  (checks model SHA256 and packages)
3. `python tools/build.py --target windows`
4. Run `python tools/benchmark.py --rows 130000` on the target hardware to confirm
   throughput and precision/recall before production use.

## Integrity

- Every dependency is version pinned in `requirements/requirements-lock.txt`.
- `models/model_manifest.json` records the expected SHA256; the loader refuses an
  unverified model (`app/ai/model_loader.py`).
- `models/jev-style/<release>/package_manifest.json` records repository, revision,
  quantization, license and SHA256/size of every file in the release;
  `JevStyleModelManager` refuses a model whose GGUF hash does not match
  (`app/ai/jev/model_manager.py`).
- Network access is not used anywhere in the analysis path: no API client exists,
  and the GGUF/tokenizer/scorer are read from the bundled local files. Models are
  downloaded only by `tools/jev_download_models.py` on a connected dev machine.
