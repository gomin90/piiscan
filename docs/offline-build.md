# Offline (air-gapped) build

Everything needed for a closed-network build is prepared in advance; no install
script touches the internet.

## Bundle contents

```
vendor/wheels/          PySide6, PyInstaller, llama-cpp-python, psutil wheels
models/                 GGUF model + model_manifest.json (sha256)
resources/              prompt and rule files
tools/                  build.py, benchmark.py
tests/                  synthetic fixtures only, no real personal data
```

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
- Network access is not used anywhere in the analysis path: no API client exists.
