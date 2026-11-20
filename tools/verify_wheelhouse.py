#!/usr/bin/env python3
"""Offline wheelhouse integrity check.

pip on an air-gapped Windows machine can only satisfy a requirement if the
required wheel is also inside `vendor/wheels`. This walks every wheel's
`Requires-Dist` lines and verifies the closure, evaluating the marker forms that
actually appear in this wheelhouse.

    python tools/verify_wheelhouse.py
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WHEEL_DIR = ROOT / "vendor" / "wheels"
PYTHON_VERSION = "3.12"

# Deliberately not shipped: the legacy LocalLLM path has no prebuilt win_amd64 wheel.
OPTIONAL = {"llama-cpp-python"}


def wheel_name(name: str) -> str:
    """Package name from a wheel filename or a Requires-Dist entry."""
    if name.endswith(".whl"):
        token = name.split("-")[0]
    else:
        token = re.split(r"[=<>\!~(\[\s]", name, maxsplit=1)[0]
    return token.lower().replace("_", "-")


def requirements_of(wheel: Path) -> list[tuple[str, str]]:
    """Return (package, marker) for every Requires-Dist line in a wheel."""
    with zipfile.ZipFile(wheel) as zf:
        meta_name = next((n for n in zf.namelist() if n.endswith(".dist-info/METADATA")), None)
        if not meta_name:
            return []
        text = zf.read(meta_name).decode("utf-8", "ignore")
    reqs: list[tuple[str, str]] = []
    for line in text.splitlines():
        if not line.startswith("Requires-Dist:"):
            continue
        req = line.split(":", 1)[1].strip()
        name, _, marker = req.partition(";")
        name = re.split(r"[\(\[]", name, maxsplit=1)[0].strip()
        if name:
            reqs.append((name, marker.strip()))
    return reqs


def required_on_windows(marker: str) -> bool:
    """True when pip would install this dependency on Windows / CPython 3.12."""
    if not marker:
        return True
    if "extra ==" in marker:
        return False
    if re.search(r"sys_platform\s*==\s*['\"]win", marker):
        return True
    if re.search(r"platform_system\s*==\s*['\"]Windows", marker):
        return True
    if re.search(r"sys_platform|platform_system", marker):
        return False  # conditioned on a different OS
    match = re.search(r"python_version\s*<\s*['\"](\d+)\.(\d+)['\"]", marker)
    if match:
        limit = (int(match.group(1)), int(match.group(2)))
        major, minor = (int(part) for part in PYTHON_VERSION.split(".")[:2])
        return (major, minor) < limit
    return True


def main() -> int:
    wheels = sorted(WHEEL_DIR.glob("*.whl"))
    if not wheels:
        print(f"[fail] {WHEEL_DIR} is empty")
        return 1

    available = {wheel_name(wheel.name) for wheel in wheels}
    missing: dict[str, set[str]] = {}
    skipped: dict[str, set[str]] = {}

    for wheel in wheels:
        for req, marker in requirements_of(wheel):
            name = wheel_name(req)
            if not required_on_windows(marker):
                if name not in available:
                    skipped.setdefault(wheel_name(wheel.name), set()).add(req)
                continue
            if name in available or name in OPTIONAL:
                continue
            missing.setdefault(wheel_name(wheel.name), set()).add(req)

    print(f"wheelhouse: {len(wheels)} wheels, {sum(w.stat().st_size for w in wheels) / 1024**2:.1f} MB")
    print(f"packages available: {len(available)}")
    if missing:
        print("[fail] a dependency pip needs on Windows is not in the wheelhouse:")
        for pkg, deps in sorted(missing.items()):
            print(f"  {pkg} requires {', '.join(sorted(deps))}")
        return 1
    print(f"[ok] every dependency pip needs on Windows/CPython {PYTHON_VERSION} is present")
    if skipped:
        print("[note] conditional requirements not shipped:")
        for pkg, deps in sorted(skipped.items()):
            print(f"  {pkg}: {', '.join(sorted(deps))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
