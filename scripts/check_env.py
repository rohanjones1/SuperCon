"""M0 environment check: one run, one compact PASS/FAIL summary.

Usage:  uv run python scripts/check_env.py
Never prints secret values.
"""
from __future__ import annotations

import importlib
import importlib.metadata as md
import os
import platform
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MP_TEST_ID = "mp-763"  # expected MgB2 (conventional, ambient-pressure SC); formula is printed, not assumed

results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""), flush=True)


def short_err(e: BaseException) -> str:
    return f"{type(e).__name__}: {str(e).splitlines()[0][:200] if str(e) else ''}"


def load_dotenv_minimal(path: Path) -> bool:
    """Load KEY=VALUE lines from .env without overriding existing env vars. No extra dependency."""
    if not path.exists():
        return False
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip().removeprefix("export ").strip()
        v = v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)
    return True


# 1. Python version
pv = sys.version_info
record("python >= 3.12", pv >= (3, 12), f"{platform.python_version()} at {sys.executable}")

# 2. Library imports (module name, distribution name for version lookup)
LIBS = [
    ("pymatgen.core", "pymatgen"),
    ("mp_api.client", "mp-api"),
    ("torch", "torch"),
    ("chgnet", "chgnet"),
    ("ase", "ase"),
    ("phonopy", "phonopy"),
    ("matminer", "matminer"),
    ("sklearn", "scikit-learn"),
    ("jarvis", "jarvis-tools"),
    ("pandas", "pandas"),
    ("numpy", "numpy"),
]
for mod, dist in LIBS:
    try:
        importlib.import_module(mod)
        try:
            ver = md.version(dist)
        except md.PackageNotFoundError:
            ver = "?"
        record(f"import {mod}", True, ver)
    except Exception as e:  # noqa: BLE001
        record(f"import {mod}", False, short_err(e))

# 3. .env hygiene + MAT_PROJECT_API present
env_path = ROOT / ".env"
loaded = load_dotenv_minimal(env_path)
gitignore = ROOT / ".gitignore"
if loaded:
    ignored = gitignore.exists() and any(
        ln.strip() in {".env", "/.env", "*.env", ".env*"} for ln in gitignore.read_text(encoding="utf-8").splitlines()
    )
    record(".env is gitignored", ignored, "" if ignored else "add '.env' to .gitignore before committing")

api_key = os.environ.get("MAT_PROJECT_API", "").strip()
record("MAT_PROJECT_API present", bool(api_key), f"length={len(api_key)}" + (" (from .env)" if loaded else ""))

# 4. Fetch one structure from Materials Project
structure = None
if api_key:
    try:
        from mp_api.client import MPRester

        t0 = time.perf_counter()
        with MPRester(api_key=api_key) as mpr:
            structure = mpr.get_structure_by_material_id(MP_TEST_ID)
        dt = time.perf_counter() - t0
        record(
            f"MP fetch {MP_TEST_ID}",
            structure is not None,
            f"{structure.composition.reduced_formula}, {len(structure)} sites, {dt:.2f}s",
        )
    except Exception as e:  # noqa: BLE001
        record(f"MP fetch {MP_TEST_ID}", False, short_err(e))
else:
    record(f"MP fetch {MP_TEST_ID}", False, "skipped: no API key")

# 5. CHGNet load + predict on that structure
if structure is not None:
    try:
        from chgnet.model import CHGNet

        t0 = time.perf_counter()
        model = CHGNet.load()
        t_load = time.perf_counter() - t0
        t0 = time.perf_counter()
        pred = model.predict_structure(structure)
        t_pred = time.perf_counter() - t0
        keys = sorted(pred.keys()) if hasattr(pred, "keys") else type(pred).__name__
        e = float(pred["e"]) if "e" in pred else float("nan")
        record(
            "CHGNet load + predict",
            True,
            f"load {t_load:.2f}s, predict {t_pred:.2f}s, keys={keys}, e={e:.4f} eV/atom (TOTAL energy, not formation)",
        )
    except Exception as e:  # noqa: BLE001
        record("CHGNet load + predict", False, short_err(e))
else:
    record("CHGNet load + predict", False, "skipped: no structure from MP")

# 6. SQLite write/read
try:
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "check.sqlite"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        con.execute("INSERT INTO t (v) VALUES (?)", ("hello",))
        con.commit()
        row = con.execute("SELECT v FROM t").fetchone()
        con.close()
    record("SQLite write/read", row == ("hello",), f"sqlite {sqlite3.sqlite_version}")
except Exception as e:  # noqa: BLE001
    record("SQLite write/read", False, short_err(e))

# Summary
n_fail = sum(1 for _, ok, _ in results if not ok)
print("\n" + "=" * 60)
print(f"SUMMARY: {len(results) - n_fail}/{len(results)} PASS" + (f", {n_fail} FAIL" if n_fail else ""))
print(f"platform: {platform.platform()} | machine: {platform.machine()}")
for name, ok, detail in results:
    if not ok:
        print(f"  FAIL {name}: {detail}")
sys.exit(1 if n_fail else 0)
