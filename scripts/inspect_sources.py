"""scripts/inspect_sources.py — M2a: field/schema discovery for each data source.

Prints field names and one example record per source, then exits.
Caches anything downloaded under data/raw/.
Usage:  uv run python scripts/inspect_sources.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

RAW = ROOT / "data" / "raw"
RAW.mkdir(parents=True, exist_ok=True)


def _trim(v: object, maxlen: int = 200) -> object:
    """Truncate large values for readable display."""
    s = str(v)
    if len(s) > maxlen:
        return f"<{type(v).__name__} len={len(v) if hasattr(v, '__len__') else '?'} truncated>"
    return v


def _print_record(rec: dict) -> None:
    trimmed = {k: _trim(v) for k, v in rec.items()}
    print(json.dumps(trimmed, indent=2, default=str))


def section(title: str) -> None:
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


# ── 1. JARVIS-DFT ──────────────────────────────────────────────────────────
section("SOURCE 1: JARVIS-DFT (jarvis-tools)")

try:
    from jarvis.db.figshare import data as jdata

    # Priority order: small superconductor dataset first, then general 3D DFT.
    # jarvis-tools caches downloads in ~/.jarvis/; we save a sample to data/raw/.
    JARVIS_DATASETS = ["supercon_3d", "supercon_bcs", "dft_3d"]
    records = []
    used_name = None

    for ds_name in JARVIS_DATASETS:
        sample_cache = RAW / f"jarvis_{ds_name}_sample.json"
        if sample_cache.exists():
            print(f"[cache] {sample_cache}")
            records = json.loads(sample_cache.read_text())
            used_name = ds_name
            print(f"Loaded {len(records)} cached records from {ds_name}")
            break
        print(f"Trying dataset '{ds_name}' (first run will download) ...")
        try:
            records = jdata(ds_name)
            if records:
                used_name = ds_name
                sample = records[:5]
                sample_cache.write_text(json.dumps(sample, indent=2, default=str))
                print(f"Downloaded {len(records)} records; cached first 5 → {sample_cache}")
                break
        except Exception as e:
            print(f"  {ds_name} unavailable: {e}")

    if records:
        example = records[0]
        fields = sorted(example.keys()) if isinstance(example, dict) else []
        tc_fields = [
            f for f in fields
            if any(kw in f.lower() for kw in ("tc", "super", "bcs", "gap", "lambda", "phonon"))
        ]
        print(f"\nDataset : {used_name}  |  records available: {len(records)}")
        print(f"Fields  ({len(fields)}): {', '.join(fields)}")
        print(f"Superconductivity-related fields: {tc_fields or '(none — check manually)'}")
        print("\nExample record [0]:")
        _print_record(example if isinstance(example, dict) else {"raw": str(example)})
    else:
        print("ERROR: no records retrieved from any JARVIS dataset")

except ImportError as e:
    print(f"jarvis-tools import failed: {e}")
except Exception as e:
    print(f"JARVIS fetch failed: {type(e).__name__}: {e}")

# ── 2. Materials Project ────────────────────────────────────────────────────
section("SOURCE 2: Materials Project (mp-api)  — mp-763 MgB2")

try:
    from lab.config import get_api_key

    api_key = get_api_key()
    if not api_key:
        print("ERROR: MAT_PROJECT_API not set — skipping")
        sys.exit(1)

    mp_cache = RAW / "mp_mp-763_summary.json"

    if mp_cache.exists():
        print(f"[cache] {mp_cache}")
        doc = json.loads(mp_cache.read_text())
    else:
        from mp_api.client import MPRester

        print("Fetching mp-763 (MgB2) summary ...")
        with MPRester(api_key=api_key) as mpr:
            result = mpr.summary.get_data_by_id("mp-763")

        # Handle both pydantic v1 (.dict()) and v2 (.model_dump())
        if hasattr(result, "model_dump"):
            doc = result.model_dump()
        elif hasattr(result, "dict"):
            doc = result.dict()
        else:
            doc = vars(result)

        mp_cache.write_text(json.dumps(doc, indent=2, default=str))
        print(f"Cached → {mp_cache}")

    fields = sorted(doc.keys()) if isinstance(doc, dict) else []
    print(f"\nSummaryDoc fields ({len(fields)}): {', '.join(fields)}")
    print("\nExample record (mp-763, MgB2):")
    _print_record(doc)

except ImportError as e:
    print(f"mp-api import failed: {e}")
except Exception as e:
    print(f"MP fetch failed: {type(e).__name__}: {e}")
