"""scripts/calibrate_stability.py — M4a: CHGNet vs MP stability calibration.

Verifies addendum + amendment, fetches GGA_GGA+U entries (cached), selects
up to 40 compounds deterministically, relaxes with CHGNet, computes leave-one-out
hull distances under corrected and uncorrected conventions, writes reports.

Usage:
    uv run python scripts/calibrate_stability.py [--ledger PATH] [--select-only]

--select-only: fetch + selection only; print per-band counts and chosen ids, exit.

Network calls only when cache files under data/raw/ are absent.
Amendment A changes (before any metric was seen):
  - Competing entries: GGA_GGA+U only (additional_criteria), no compatible_only toggle.
  - Uncorrected convention: uncorrected_energy_per_atom of the same entries.
  - Ground truth: energy_above_hull from GGA_GGA+U thermo doc (summary kept as ref column).
  - Compounds with no GGA_GGA+U thermo doc: excluded and printed.
  - Partial CSV: append each compound immediately; skip on rerun.

Integrity fixes (post-calibration):
  - Classification uses the frozen convention column (not a hardcoded fallback).
  - Missing/non-finite hull predictions are excluded from TP/FP/TN/FN, not counted
    as predicted-unstable.  n_missing_predictions is reported separately.
  - Resumed partial CSV: blank error fields (NaN from pandas) are correctly
    treated as successful rows.
  - Leave-one-out integrity guard: verifies mat_id appears exactly once in the
    cached GGA_GGA+U entries before building the reference hull.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from lab.config import get_api_key
from lab.ledger import (
    DEFAULT_PATH as LEDGER_DEFAULT,
    init_db,
    record_prereg,
    record_result,
    record_run,
    record_timing,
)
from lab.relax import relax_structure
from lab.stability import e_above_hull
from pymatgen.analysis.phase_diagram import PDEntry
from pymatgen.core import Composition

ADDENDUM_PATH  = Path("experiments/prereg/addendum_stability_001.yaml")
AMENDMENT_PATH = Path("experiments/prereg/addendum_stability_001_amendment_a.yaml")
RAW_DIR        = Path("data/raw")
REPORTS_DIR    = Path("reports")

BANDS: list[tuple[float, float, int, str]] = [
    (0.00, 0.05, 14, "[0.00,0.05)"),
    (0.05, 0.10,  8, "[0.05,0.10)"),
    (0.10, 0.20,  9, "[0.10,0.20)"),
    (0.20, 0.30,  9, "[0.20,0.30]"),
]
N_TARGET = sum(b[2] for b in BANDS)  # 40

_ALKALI        = {"Li", "Na", "K", "Rb", "Cs"}
_ALKALINE_EARTH = {"Be", "Mg", "Ca", "Sr", "Ba"}
_TRANSITION    = {
    "Sc","Ti","V","Cr","Mn","Fe","Co","Ni","Cu","Zn",
    "Y","Zr","Nb","Mo","Tc","Ru","Rh","Pd","Ag","Cd",
    "Hf","Ta","W","Re","Os","Ir","Pt","Au","Hg",
    "La","Ce","Pr","Nd","Pm","Sm","Eu","Gd","Tb",
    "Dy","Ho","Er","Tm","Yb","Lu",
}
_METALS = _ALKALI | _ALKALINE_EARTH | _TRANSITION
_ANIONS = {"B", "C", "Si"}

_PARTIAL_COLS = [
    "material_id", "formula", "chemsys",
    "mp_energy_per_atom_summary", "mp_energy_above_hull_summary",
    "mp_energy_per_atom_gga", "mp_energy_above_hull_gga",
    "mp_uncorrected_energy_per_atom",
    "chgnet_energy_per_atom",
    "chgnet_hull_corrected", "chgnet_hull_uncorrected",
    "n_steps", "wall_ms", "error",
]


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mat_sha(mat_id: str) -> str:
    return hashlib.sha256(mat_id.encode()).hexdigest()


def _is_target_binary(elements: list[str]) -> bool:
    el = set(elements)
    return len(el) == 2 and len(el & _ANIONS) == 1 and len(el & _METALS) == 1


def _band_idx(h: float) -> int:
    if h < 0.05:    return 0
    elif h < 0.10:  return 1
    elif h < 0.20:  return 2
    elif h <= 0.30: return 3
    return -1


def _wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    z2 = z * z
    denom = 1 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    margin = z * (p * (1 - p) / n + z2 / (4 * n * n)) ** 0.5 / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


# ---------------------------------------------------------------------------
# Leave-one-out integrity guard
# ---------------------------------------------------------------------------

def _check_loso_integrity(gga_records: list[dict], mat_id: str) -> None:
    """Verify that leave-one-out exclusion is well-defined before building the hull.

    Raises RuntimeError (with a descriptive message) if:
      - *mat_id* does not appear in *gga_records* as an ``entry_id``  → the
        reference hull would NOT be leave-one-out (target is already absent).
      - *mat_id* appears more than once → ambiguous exclusion.

    This guard prevents silent errors where a non-LOO hull is used for
    calibration without any warning.
    """
    n_before = len(gga_records)
    matching = [r for r in gga_records if r["entry_id"] == mat_id]
    n_matched = len(matching)
    if n_matched == 0:
        raise RuntimeError(
            f"Leave-one-out integrity failure: '{mat_id}' not found "
            f"in {n_before} fetched/cached GGA_GGA+U entries (matched by entry_id). "
            "The hull would not be leave-one-out. Check that the cache is not stale "
            "or that entry_id matches material_id for this compound."
        )
    if n_matched != 1:
        raise RuntimeError(
            f"Leave-one-out integrity failure: '{mat_id}' matches {n_matched} "
            f"entries in {n_before} GGA_GGA+U records (expected exactly 1). "
            "Possible duplicate entry_id in cache. Cannot build unambiguous LOO hull."
        )


# ---------------------------------------------------------------------------
# Classification metrics (frozen-convention-aware, missing-prediction-safe)
# ---------------------------------------------------------------------------

def _compute_classification(
    ok_rows: list[dict],
    frozen: str,
    mp_hull_arr: "np.ndarray",
    threshold: float = 0.05,
) -> dict:
    """Compute stability classification metrics using the frozen hull convention.

    Only rows whose frozen hull prediction is *finite* are classified.
    Rows with a missing or non-finite prediction are excluded and counted
    in ``n_missing``; they are NOT treated as predicted-unstable (DEPRIORITIZE).

    Parameters
    ----------
    ok_rows : list[dict]
        Successful result rows (error field absent/None/NaN).
    frozen : str
        ``"corrected"`` or ``"uncorrected"`` — selects ``chgnet_hull_{frozen}``.
    mp_hull_arr : np.ndarray
        MP GGA/GGA+U energy-above-hull values, aligned with *ok_rows*.
    threshold : float
        MP hull threshold for "stable" (pre-registered: 0.05 eV/atom).

    Returns
    -------
    dict with keys:
        tp, fp, tn, fn, n_stable, n_missing, n_classified,
        precision, recall, prec_ci, rec_ci, recall_indet
    """
    key = f"chgnet_hull_{frozen}"
    pred_raw = np.array(
        [r[key] if r[key] is not None else float("nan") for r in ok_rows],
        dtype=float,
    )

    # Exclude non-finite predictions — do NOT count them as predicted-unstable
    finite_mask  = np.isfinite(pred_raw)
    n_missing    = int((~finite_mask).sum())
    n_classified = int(finite_mask.sum())

    if n_classified == 0:
        return {
            "tp": 0, "fp": 0, "tn": 0, "fn": 0,
            "n_stable": 0, "n_missing": n_missing, "n_classified": 0,
            "precision": float("nan"), "recall": float("nan"),
            "prec_ci": (float("nan"), float("nan")),
            "rec_ci":  (float("nan"), float("nan")),
            "recall_indet": True,
        }

    pred        = pred_raw[finite_mask]
    mp_hull_cls = mp_hull_arr[finite_mask]

    pred_pos = pred        <= threshold
    true_pos = mp_hull_cls <= threshold
    n_stable = int(true_pos.sum())

    tp = int((pred_pos &  true_pos).sum())
    fp = int((pred_pos & ~true_pos).sum())
    tn = int((~pred_pos & ~true_pos).sum())
    fn = int((~pred_pos &  true_pos).sum())

    precision = tp / (tp + fp) if (tp + fp) > 0 else float("nan")
    recall    = tp / (tp + fn) if (tp + fn) > 0 else float("nan")

    return {
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "n_stable": n_stable, "n_missing": n_missing, "n_classified": n_classified,
        "precision": precision, "recall": recall,
        "prec_ci": _wilson_ci(tp, tp + fp),
        "rec_ci":  _wilson_ci(tp, tp + fn),
        "recall_indet": n_stable < 10,
    }


# ---------------------------------------------------------------------------
# MP cache helpers — all keys include chemsys + thermo_type
# ---------------------------------------------------------------------------

def _load_or_fetch_summary(cache_path: Path) -> list[dict]:
    """Binary metal B/C/Si compounds from MP summary (cached, no theoretical filter)."""
    if cache_path.exists():
        print(f"  Summary cache hit: {cache_path}")
        return json.loads(cache_path.read_text())

    print("  Fetching summary from Materials Project ...")
    from mp_api.client import MPRester
    records: list[dict] = []
    with MPRester(api_key=get_api_key()) as mpr:
        for anion in ("B", "C", "Si"):
            results = mpr.materials.summary.search(
                elements=[anion],
                num_elements=2,
                is_metal=True,
                energy_above_hull=(0.0, 0.30),
                fields=[
                    "material_id", "formula_pretty",
                    "energy_per_atom", "uncorrected_energy_per_atom",
                    "energy_above_hull", "nsites",
                ],
            )
            for r in results:
                comp = Composition(r.formula_pretty)
                elements = [str(el) for el in comp.elements]
                if not _is_target_binary(elements):
                    continue
                records.append({
                    "material_id": r.material_id,
                    "formula": r.formula_pretty,
                    "elements": elements,
                    "anion": anion,
                    "energy_per_atom": r.energy_per_atom,
                    "uncorrected_energy_per_atom": getattr(r, "uncorrected_energy_per_atom", None),
                    "energy_above_hull": r.energy_above_hull,
                    "nsites": r.nsites,
                })

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(records, indent=2))
    print(f"  Cached {len(records)} entries -> {cache_path}")
    return records


def _load_or_fetch_entries_gga(chemsys: str, cache_dir: Path) -> list[dict]:
    """
    GGA_GGA+U competing entries for a chemical system (Amendment A).
    Cache key includes chemsys and thermo_type; old _corr/_uncorr files not reused.

    BUG FIX: composition stored as {str(k): v} to avoid Element-keyed dict
    JSON serialization failure ("keys must be str, not Element").

    Returns list of dicts: {entry_id, composition, corrected_energy,
    uncorrected_energy, uncorrected_energy_per_atom}
    """
    cache_path = cache_dir / f"{chemsys}_GGA_GGA+U.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text())

    from mp_api.client import MPRester
    with MPRester(api_key=get_api_key()) as mpr:
        entries = mpr.get_entries_in_chemsys(
            chemsys.split("-"),
            additional_criteria={"thermo_types": ["GGA_GGA+U"]},
        )

    records = []
    for e in entries:
        # Explicit str() conversion on composition keys — fixes Element-keyed dict bug
        comp_dict = {str(k): float(v) for k, v in e.composition.items()}
        n_atoms = e.composition.num_atoms
        records.append({
            "entry_id": str(getattr(e, "entry_id", "")),
            "composition": comp_dict,
            "corrected_energy": float(e.energy),
            "uncorrected_energy": float(e.uncorrected_energy),
            "uncorrected_energy_per_atom": float(e.uncorrected_energy / n_atoms),
        })

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(records))  # all fields are str/float — safe
    return records


def _load_or_fetch_thermo_gt(mat_id: str, gt_cache_dir: Path) -> dict | None:
    """
    GGA_GGA+U thermo doc for one material (ground truth: energy_above_hull,
    energy_per_atom). Returns None if no GGA_GGA+U doc exists.
    Cache key: {mat_id}_GGA_GGA+U.json
    """
    cache_path = gt_cache_dir / f"{mat_id}_GGA_GGA+U.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text())  # may be null (json None)

    from mp_api.client import MPRester
    with MPRester(api_key=get_api_key()) as mpr:
        docs = mpr.materials.thermo.search(
            material_ids=[mat_id],
            thermo_types=["GGA_GGA+U"],
            fields=["material_id", "energy_above_hull", "energy_per_atom"],
        )

    if not docs:
        cache_path.write_text(json.dumps(None))
        return None

    doc = docs[0]
    result = {
        "material_id": doc.material_id,
        "energy_above_hull": float(doc.energy_above_hull),
        "energy_per_atom": float(doc.energy_per_atom),
    }
    cache_path.write_text(json.dumps(result))
    return result


def _build_ref_entries(
    gga_records: list[dict],
    exclude_mat_id: str,
    corrected: bool,
) -> list[PDEntry]:
    """Build PDEntry list for PhaseDiagram, excluding the target material_id."""
    result = []
    for r in gga_records:
        if r["entry_id"] == exclude_mat_id:
            continue
        comp = Composition(r["composition"])
        energy = r["corrected_energy"] if corrected else r["uncorrected_energy"]
        result.append(PDEntry(comp, energy, name=r["entry_id"]))
    return result


def _load_or_fetch_structure(mat_id: str, cache_dir: Path):
    from pymatgen.core import Structure
    p = cache_dir / f"{mat_id}.json"
    if p.exists():
        return Structure.from_dict(json.loads(p.read_text()))
    from mp_api.client import MPRester
    with MPRester(api_key=get_api_key()) as mpr:
        structure = mpr.get_structure_by_material_id(mat_id)
    p.write_text(json.dumps(structure.as_dict()))
    return structure


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

def _select_calibration_set(records: list[dict]) -> list[dict]:
    bins: list[list[dict]] = [[] for _ in BANDS]
    for r in records:
        i = _band_idx(r["energy_above_hull"])
        if i >= 0:
            bins[i].append(r)

    selected: list[dict] = []
    chemsys_count: dict[str, int] = {}

    print("  Selection (addendum bands, sha256 order, 2-per-chemsys cap):")
    for i, (lo, hi, n_target, label) in enumerate(BANDS):
        bin_sorted = sorted(bins[i], key=lambda r: _mat_sha(r["material_id"]))
        taken = 0
        for r in bin_sorted:
            if taken >= n_target:
                break
            chemsys = "-".join(sorted(r["elements"]))
            if chemsys_count.get(chemsys, 0) >= 2:
                continue
            selected.append(r)
            chemsys_count[chemsys] = chemsys_count.get(chemsys, 0) + 1
            taken += 1
        sf = f"  SHORTFALL={n_target - taken}" if taken < n_target else ""
        print(f"    band {label}: requested={n_target}  available={len(bins[i])}  selected={taken}{sf}")

    print(f"  Total selected: {len(selected)}/{N_TARGET}")
    return selected


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", type=Path, default=LEDGER_DEFAULT)
    parser.add_argument("--select-only", action="store_true",
                        help="Fetch and print selection only; do not relax.")
    args = parser.parse_args()

    init_db(args.ledger)

    # ---- Verify and record addendum + amendment ----
    for path, label in [(ADDENDUM_PATH, "Addendum"), (AMENDMENT_PATH, "Amendment A")]:
        if not path.exists():
            raise FileNotFoundError(f"{label} not found: {path}")
        content = yaml.safe_load(path.read_text())
        ph = record_prereg(content, path=args.ledger)
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        print(f"{label} SHA256: {sha}  ledger hash: {ph}")

    # ---- Fetch summary + select ----
    records = _load_or_fetch_summary(RAW_DIR / "mp_calib_summary.json")
    print(f"  Total qualifying records: {len(records)}")
    selected = _select_calibration_set(records)

    if args.select_only:
        print(f"\n{'='*70}")
        print(f"  {'material_id':<15}  {'formula':<12}  {'chemsys':<8}  {'mp_hull':>7}")
        for r in selected:
            cs = "-".join(sorted(r["elements"]))
            print(f"  {r['material_id']:<15}  {r['formula']:<12}  {cs:<8}  {r['energy_above_hull']:>7.4f}")
        print(f"{'='*70}")
        return

    # ---- Cache directories ----
    struct_cache = RAW_DIR / "mp_calib_structures"
    hull_cache   = RAW_DIR / "mp_calib_hull_entries"
    gt_cache     = RAW_DIR / "mp_calib_gt"
    for d in (struct_cache, hull_cache, gt_cache):
        d.mkdir(parents=True, exist_ok=True)

    # ---- Partial CSV: load existing, build skip set ----
    # FIX: use pd.isna() to distinguish blank error fields (NaN from pandas)
    # from real error strings. NaN means the row was successful on the previous run.
    partial_path = REPORTS_DIR / "stability_calibration_partial.csv"
    REPORTS_DIR.mkdir(exist_ok=True)
    rows: list[dict] = []
    processed_ids: set[str] = set()
    if partial_path.exists():
        existing = pd.read_csv(partial_path).to_dict("records")
        rows = existing
        processed_ids = {r["material_id"] for r in existing}
        print(f"  Partial CSV: {len(processed_ids)} already processed, skipping.")

    # ---- Ledger run ----
    t_start = _utc_iso()
    wall_t0 = time.time()
    run_id = record_run(
        tool="stability_calibration",
        tool_version="chgnet+pymatgen+GGA_GGA+U",
        model_checkpoint="CHGNet-default",
        seed=None,
        hardware=f"{platform.node()} {platform.processor()}",
        wall_ms=0.0,
        path=args.ledger,
    )
    print(f"Ledger run_id={run_id}")

    excluded_no_gt: list[str] = []

    for rec in selected:
        mat_id  = rec["material_id"]
        formula = rec["formula"]
        chemsys = "-".join(sorted(rec["elements"]))

        if mat_id in processed_ids:
            print(f"  Skipping {mat_id} (in partial CSV)")
            continue

        mp_epa_summary  = rec["energy_per_atom"]
        mp_hull_summary = rec["energy_above_hull"]

        print(f"  {mat_id} ({formula:12s}) summary_hull={mp_hull_summary:.3f}", end="  ", flush=True)

        # Ground truth from GGA_GGA+U thermo doc
        gt = _load_or_fetch_thermo_gt(mat_id, gt_cache)
        if gt is None:
            print(f"EXCLUDED: no GGA_GGA+U thermo doc")
            excluded_no_gt.append(f"{mat_id} ({formula})")
            row = _make_row(rec, chemsys, error="no_GGA_GGA+U_thermo_doc")
            _append_partial(row, partial_path)
            rows.append(row)
            continue

        mp_epa_gga  = gt["energy_per_atom"]
        mp_hull_gga = gt["energy_above_hull"]

        # GGA_GGA+U competing entries (used for hull and uncorrected epa)
        try:
            gga_records = _load_or_fetch_entries_gga(chemsys, hull_cache)
        except Exception as exc:
            print(f"ENTRIES_FAIL: {exc}")
            row = _make_row(rec, chemsys, mp_epa_gga=mp_epa_gga,
                            mp_hull_gga=mp_hull_gga, error=str(exc))
            _append_partial(row, partial_path)
            rows.append(row)
            continue

        # Uncorrected energy per atom for metric (a), from the same GGA_GGA+U entries
        target_entry = next((r for r in gga_records if r["entry_id"] == mat_id), None)
        mp_uncorr_epa = target_entry["uncorrected_energy_per_atom"] if target_entry else None

        # Structure
        try:
            structure = _load_or_fetch_structure(mat_id, struct_cache)
        except Exception as exc:
            print(f"STRUCT_FAIL: {exc}")
            row = _make_row(rec, chemsys, mp_epa_gga=mp_epa_gga,
                            mp_hull_gga=mp_hull_gga,
                            mp_uncorr_epa=mp_uncorr_epa, error=str(exc))
            _append_partial(row, partial_path)
            rows.append(row)
            continue

        # CHGNet relax
        res = relax_structure(structure)
        if res["error"]:
            print(f"RELAX_FAIL: {res['error']}")
            row = _make_row(rec, chemsys, mp_epa_gga=mp_epa_gga,
                            mp_hull_gga=mp_hull_gga,
                            mp_uncorr_epa=mp_uncorr_epa,
                            wall_ms=res["wall_ms"], error=res["error"])
            _append_partial(row, partial_path)
            rows.append(row)
            continue

        chgnet_epa = res["energy_per_atom"]
        relaxed    = res["relaxed_structure"]

        # Leave-one-out integrity guard — must run before any hull computation.
        # Fails loudly if mat_id is absent from the cached entries or duplicated.
        try:
            _check_loso_integrity(gga_records, mat_id)
        except RuntimeError as exc:
            print(f"\n  LOSO_INTEGRITY_FAIL: {exc}")
            row = _make_row(rec, chemsys, mp_epa_gga=mp_epa_gga,
                            mp_hull_gga=mp_hull_gga,
                            mp_uncorr_epa=mp_uncorr_epa,
                            wall_ms=res["wall_ms"], error=str(exc))
            _append_partial(row, partial_path)
            rows.append(row)
            continue

        # Leave-one-out hull: corrected reference
        hull_corr: float | None = None
        try:
            ref_corr = _build_ref_entries(gga_records, mat_id, corrected=True)
            hull_corr = e_above_hull(chgnet_epa, relaxed, ref_corr)
        except Exception as exc:
            print(f"HULL_CORR_FAIL:{exc}", end="  ")

        # Leave-one-out hull: uncorrected reference (same entries, raw energies)
        hull_uncorr: float | None = None
        try:
            ref_uncorr = _build_ref_entries(gga_records, mat_id, corrected=False)
            hull_uncorr = e_above_hull(chgnet_epa, relaxed, ref_uncorr)
        except Exception as exc:
            print(f"HULL_UNCORR_FAIL:{exc}", end="  ")

        print(f"chgnet_epa={chgnet_epa:.3f}  hull_c={hull_corr}  steps={res['n_steps']}")

        row = {
            "material_id": mat_id, "formula": formula, "chemsys": chemsys,
            "mp_energy_per_atom_summary": mp_epa_summary,
            "mp_energy_above_hull_summary": mp_hull_summary,
            "mp_energy_per_atom_gga": mp_epa_gga,
            "mp_energy_above_hull_gga": mp_hull_gga,
            "mp_uncorrected_energy_per_atom": mp_uncorr_epa,
            "chgnet_energy_per_atom": chgnet_epa,
            "chgnet_hull_corrected": hull_corr,
            "chgnet_hull_uncorrected": hull_uncorr,
            "n_steps": res["n_steps"], "wall_ms": res["wall_ms"], "error": None,
        }
        _append_partial(row, partial_path)
        rows.append(row)

        record_result(run_id, None, 1, f"chgnet_epa:{mat_id}", chgnet_epa, path=args.ledger)
        if hull_corr is not None:
            record_result(run_id, None, 1, f"hull_corr:{mat_id}", hull_corr, path=args.ledger)

    t_end = _utc_iso()
    wall_ms_total = (time.time() - wall_t0) * 1000
    record_timing("stability_calibration", t_start, t_end, path=args.ledger)

    if excluded_no_gt:
        print(f"\n  Excluded (no GGA_GGA+U thermo doc): {excluded_no_gt}")

    # ---- Metrics (only successful rows) ----
    # FIX: use pd.isna() so that blank error fields from a resumed CSV (NaN in
    # pandas) are correctly identified as successful rows, not failures.
    ok = [r for r in rows if pd.isna(r.get("error"))]
    n_ok, n_fail = len(ok), len(rows) - len(ok)
    if n_fail:
        print(f"\n  {n_fail} failed/excluded (see partial CSV)")
    if n_ok == 0:
        print("No successful relaxations. Exiting.")
        return

    mp_epa_arr     = np.array([r["mp_energy_per_atom_gga"] for r in ok], dtype=float)
    mp_epa_u_arr   = np.array([r["mp_uncorrected_energy_per_atom"] or float("nan") for r in ok], dtype=float)
    mp_hull_arr    = np.array([r["mp_energy_above_hull_gga"] for r in ok], dtype=float)
    chgnet_epa_arr = np.array([r["chgnet_energy_per_atom"] for r in ok], dtype=float)

    mae_epa_corr   = float(np.nanmean(np.abs(chgnet_epa_arr - mp_epa_arr)))
    mae_epa_uncorr = float(np.nanmean(np.abs(chgnet_epa_arr - mp_epa_u_arr)))
    better_epa     = "corrected" if mae_epa_corr <= mae_epa_uncorr else "uncorrected"

    def _hull_mae(key: str) -> float:
        pred = np.array([r[key] if r[key] is not None else float("nan") for r in ok], dtype=float)
        valid = ~np.isnan(pred)
        return float(np.mean(np.abs(pred[valid] - mp_hull_arr[valid]))) if valid.any() else float("nan")

    mae_hull_corr   = _hull_mae("chgnet_hull_corrected")
    mae_hull_uncorr = _hull_mae("chgnet_hull_uncorrected")
    valid_maes = [x for x in [mae_hull_corr, mae_hull_uncorr] if not np.isnan(x)]
    best_hull_mae = min(valid_maes) if valid_maes else float("nan")
    frozen = "corrected" if (np.isnan(mae_hull_uncorr) or mae_hull_corr <= mae_hull_uncorr) else "uncorrected"

    # ---- Classification: use frozen convention; exclude missing predictions ----
    # FIX 1: use f"chgnet_hull_{frozen}" — not a hardcoded column name.
    # FIX 2: rows where the frozen-column hull is None/NaN are excluded from
    #         TP/FP/TN/FN rather than being silently treated as predicted-unstable.
    THRESHOLD = 0.05
    cls          = _compute_classification(ok, frozen, mp_hull_arr, THRESHOLD)
    tp           = cls["tp"]
    fp           = cls["fp"]
    tn           = cls["tn"]
    fn           = cls["fn"]
    n_stable     = cls["n_stable"]
    n_missing    = cls["n_missing"]
    n_classified = cls["n_classified"]
    precision    = cls["precision"]
    recall       = cls["recall"]
    prec_ci      = cls["prec_ci"]
    rec_ci       = cls["rec_ci"]
    recall_indet = cls["recall_indet"]

    usable = best_hull_mae <= 0.06 and (recall_indet or recall >= 0.80)
    verdict = "USABLE" if usable else "NOT USABLE AS-IS"

    for metric, val in [
        ("mae_epa_vs_corrected",    mae_epa_corr),
        ("mae_epa_vs_uncorrected",  mae_epa_uncorr),
        ("mae_hull_corrected_ref",  mae_hull_corr),
        ("mae_hull_uncorrected_ref",mae_hull_uncorr),
        ("stability_precision_0.05",precision),
        ("stability_recall_0.05",   recall),
    ]:
        if not np.isnan(val):
            record_result(run_id, None, 1, metric, val, path=args.ledger)

    json_path = REPORTS_DIR / "stability_calibration.json"
    csv_path  = REPORTS_DIR / "stability_calibration.csv"
    report = {
        "run_id": run_id,
        "n_selected": len(selected),
        "n_successful": n_ok,
        "n_failed": n_fail,
        "excluded_no_gt": excluded_no_gt,
        "material_ids_used": [r["material_id"] for r in ok],
        "metrics": {
            "a_energy_per_atom_mae": {
                "vs_gga_corrected": mae_epa_corr,
                "vs_gga_uncorrected": mae_epa_uncorr,
                "better_agreement": better_epa,
            },
            "b_hull_mae": {
                "corrected_ref": mae_hull_corr,
                "uncorrected_ref": mae_hull_uncorr,
                "frozen_convention": frozen,
            },
            "c_stability_classification_at_0.05": {
                "n_stable_mp": n_stable,
                "n_classified": n_classified,
                "n_missing_predictions": n_missing,
                "tp": tp, "fp": fp, "tn": tn, "fn": fn,
                "precision": precision, "precision_wilson_ci_95": list(prec_ci),
                "recall": recall, "recall_wilson_ci_95": list(rec_ci),
                "recall_indeterminate": recall_indet,
            },
        },
        "verdict": {
            "usable": usable, "message": verdict,
            "best_hull_mae": best_hull_mae, "frozen_convention": frozen,
            "recall": recall, "recall_indeterminate": recall_indet,
            "thresholds": {"hull_mae": 0.06, "recall": 0.80},
        },
        "wall_ms_total": wall_ms_total,
    }
    with open(json_path, "w") as f:
        json.dump(report, f, indent=2)
    pd.DataFrame(rows).to_csv(csv_path, index=False)

    print(f"\n{'='*64}")
    print(f"  Stability Calibration  run_id={run_id}  n={n_ok}/{len(selected)}")
    print(f"{'='*64}")
    print(f"  (a) Energy/atom MAE vs GGA corrected:   {mae_epa_corr:.4f} eV/atom")
    print(f"      Energy/atom MAE vs GGA uncorrected:  {mae_epa_uncorr:.4f} eV/atom")
    print(f"      Better agreement: {better_epa}")
    print(f"  (b) Hull MAE (corrected ref):   {mae_hull_corr:.4f} eV/atom")
    print(f"      Hull MAE (uncorrected ref):  {mae_hull_uncorr:.4f} eV/atom")
    print(f"      *** FROZEN convention: {frozen} (lower hull MAE) ***")
    print(f"  (c) n_stable(MP_GGA)={n_stable}  n_classified={n_classified}  "
          f"n_missing_predictions={n_missing}  @ threshold 0.05 eV/atom:")
    print(f"      precision={precision:.3f}  Wilson95=[{prec_ci[0]:.3f},{prec_ci[1]:.3f}]")
    if recall_indet:
        print(f"      recall=INDETERMINATE (n_stable={n_stable} < 10)")
    else:
        print(f"      recall={recall:.3f}  Wilson95=[{rec_ci[0]:.3f},{rec_ci[1]:.3f}]")
    print(f"      Confusion: TP={tp}  FP={fp}  TN={tn}  FN={fn}")
    print(f"\n  Verdict: {verdict}")
    print(f"  Reports: {json_path}, {csv_path}, {partial_path}")
    print(f"{'='*64}\n")


def _append_partial(row: dict, path: Path) -> None:
    """Append one row to partial CSV immediately (header only if new file)."""
    df = pd.DataFrame([{c: row.get(c) for c in _PARTIAL_COLS}])
    df.to_csv(path, mode="a", header=not path.exists(), index=False)


def _make_row(
    rec: dict,
    chemsys: str,
    mp_epa_gga: float | None = None,
    mp_hull_gga: float | None = None,
    mp_uncorr_epa: float | None = None,
    wall_ms: float | None = None,
    error: str | None = None,
) -> dict:
    return {
        "material_id": rec["material_id"], "formula": rec["formula"], "chemsys": chemsys,
        "mp_energy_per_atom_summary": rec["energy_per_atom"],
        "mp_energy_above_hull_summary": rec["energy_above_hull"],
        "mp_energy_per_atom_gga": mp_epa_gga,
        "mp_energy_above_hull_gga": mp_hull_gga,
        "mp_uncorrected_energy_per_atom": mp_uncorr_epa,
        "chgnet_energy_per_atom": None, "chgnet_hull_corrected": None,
        "chgnet_hull_uncorrected": None, "n_steps": None,
        "wall_ms": wall_ms, "error": error,
    }


if __name__ == "__main__":
    main()
