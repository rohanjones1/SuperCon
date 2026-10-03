"""scripts/calibrate_stability.py — M4a: CHGNet vs MP stability calibration.

Verifies addendum_stability_001.yaml, fetches qualifying MP entries (cached),
selects up to 40 via the addendum's deterministic procedure, relaxes with CHGNet,
computes leave-one-out hull distances under two energy conventions, and writes
reports/stability_calibration.{json,csv}.

Usage:
    uv run python scripts/calibrate_stability.py [--ledger PATH] [--select-only]

--select-only: run MP fetch + selection only; print per-band counts and chosen
ids, then exit without relaxing anything.

Network calls only when cache files under data/raw/ are absent.
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

ADDENDUM_PATH = Path("experiments/prereg/addendum_stability_001.yaml")
RAW_DIR = Path("data/raw")
REPORTS_DIR = Path("reports")

# Bands from addendum: (lo_inclusive, hi_exclusive_or_inclusive, n_target, label)
# Last band is [0.20, 0.30] (inclusive on right)
BANDS: list[tuple[float, float, int, str]] = [
    (0.00, 0.05, 14, "[0.00,0.05)"),
    (0.05, 0.10,  8, "[0.05,0.10)"),
    (0.10, 0.20,  9, "[0.10,0.20)"),
    (0.20, 0.30,  9, "[0.20,0.30]"),
]
N_TARGET = sum(b[2] for b in BANDS)  # 40

# Element sets for post-filtering binary metal+{B,C,Si} compounds
_ALKALI = {"Li", "Na", "K", "Rb", "Cs"}
_ALKALINE_EARTH = {"Be", "Mg", "Ca", "Sr", "Ba"}
_TRANSITION = {
    "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn",
    "Y", "Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd",
    "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg",
    "La", "Ce", "Pr", "Nd", "Pm", "Sm", "Eu", "Gd", "Tb",
    "Dy", "Ho", "Er", "Tm", "Yb", "Lu",
}
_METALS = _ALKALI | _ALKALINE_EARTH | _TRANSITION
_ANIONS = {"B", "C", "Si"}


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
    return -1  # outside range


def _wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score 95% CI for k successes out of n trials."""
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    z2 = z * z
    denom = 1 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    margin = z * (p * (1 - p) / n + z2 / (4 * n * n)) ** 0.5 / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


# ---------------------------------------------------------------------------
# MP cache helpers
# ---------------------------------------------------------------------------

def _load_or_fetch_summary(cache_path: Path) -> list[dict]:
    """Fetch binary metal B/C/Si compounds from MP summary endpoint (cached).
    Does NOT filter on theoretical (addendum: include theoretical entries)."""
    if cache_path.exists():
        print(f"  Summary cache hit: {cache_path}")
        return json.loads(cache_path.read_text())

    print("  Fetching summary from Materials Project ...")
    from mp_api.client import MPRester
    from pymatgen.core import Composition

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


def _select_calibration_set(records: list[dict]) -> list[dict]:
    """
    Deterministic stratified selection per addendum:
    - Sort within each band by sha256(material_id) ascending.
    - Skip if chemsys already has 2 selected entries (global cap).
    - Take up to band's n; report shortfall if fewer available.
    """
    bins: list[list[dict]] = [[] for _ in BANDS]
    for r in records:
        i = _band_idx(r["energy_above_hull"])
        if i >= 0:
            bins[i].append(r)

    selected: list[dict] = []
    chemsys_count: dict[str, int] = {}  # global 2-per-chemsys cap

    print("  Selection (addendum bands):")
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
        shortfall = n_target - taken
        sf_str = f"  SHORTFALL={shortfall}" if shortfall > 0 else ""
        print(f"    band {label}: requested={n_target}  available={len(bins[i])}  selected={taken}{sf_str}")

    print(f"  Total selected: {len(selected)}/{N_TARGET}")
    return selected


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


def _load_or_fetch_entries(chemsys: str, cache_dir: Path, corrected: bool) -> list:
    """Fetch and cache all MP ComputedEntry objects for a chemical system."""
    from pymatgen.entries.computed_entries import ComputedEntry
    tag = "corr" if corrected else "uncorr"
    p = cache_dir / f"{chemsys}_{tag}.json"
    if p.exists():
        return [ComputedEntry.from_dict(d) for d in json.loads(p.read_text())]
    from mp_api.client import MPRester
    with MPRester(api_key=get_api_key()) as mpr:
        entries = mpr.get_entries_in_chemsys(chemsys.split("-"), compatible_only=corrected)
    p.write_text(json.dumps([e.as_dict() for e in entries]))
    return entries


def _exclude_target(entries: list, mat_id: str) -> list:
    """Remove entry with entry_id == mat_id; leave other polymorphs in."""
    return [e for e in entries if getattr(e, "entry_id", None) != mat_id]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", type=Path, default=LEDGER_DEFAULT)
    parser.add_argument(
        "--select-only", action="store_true",
        help="Fetch and print selection only; do not relax or compute hull.",
    )
    args = parser.parse_args()

    init_db(args.ledger)

    # ---- Verify and record addendum ----
    if not ADDENDUM_PATH.exists():
        raise FileNotFoundError(f"Addendum not found: {ADDENDUM_PATH}")
    addendum_bytes = ADDENDUM_PATH.read_bytes()
    addendum_sha = hashlib.sha256(addendum_bytes).hexdigest()
    addendum_content = yaml.safe_load(ADDENDUM_PATH.read_text())
    prereg_hash = record_prereg(addendum_content, path=args.ledger)
    print(f"Addendum SHA256 (file): {addendum_sha}")
    print(f"Ledger prereg hash:     {prereg_hash}")

    # ---- Fetch summary ----
    records = _load_or_fetch_summary(RAW_DIR / "mp_calib_summary.json")
    print(f"  Total qualifying records: {len(records)}")
    selected = _select_calibration_set(records)

    # ---- --select-only: print and exit ----
    if args.select_only:
        print(f"\n{'='*68}")
        print(f"  Selected {len(selected)} entries (--select-only; no relaxation run)")
        print(f"  {'material_id':<15}  {'formula':<12}  {'chemsys':<8}  {'mp_hull':>7}")
        for r in selected:
            chemsys = "-".join(sorted(r["elements"]))
            print(f"  {r['material_id']:<15}  {r['formula']:<12}  {chemsys:<8}  {r['energy_above_hull']:>7.4f}")
        print(f"{'='*68}")
        return

    # ---- Cache directories ----
    struct_cache = RAW_DIR / "mp_calib_structures"
    hull_cache = RAW_DIR / "mp_calib_hull_entries"
    struct_cache.mkdir(parents=True, exist_ok=True)
    hull_cache.mkdir(parents=True, exist_ok=True)

    # ---- Ledger run ----
    t_start = _utc_iso()
    wall_t0 = time.time()
    run_id = record_run(
        tool="stability_calibration",
        tool_version="chgnet+pymatgen",
        model_checkpoint="CHGNet-default",
        seed=None,
        hardware=f"{platform.node()} {platform.processor()}",
        wall_ms=0.0,
        path=args.ledger,
    )
    print(f"Ledger run_id={run_id}")

    # ---- Run calibration ----
    rows: list[dict] = []
    mat_ids_used: list[str] = []

    for rec in selected:
        mat_id = rec["material_id"]
        formula = rec["formula"]
        mp_epa = rec["energy_per_atom"]
        mp_epa_uncorr = rec.get("uncorrected_energy_per_atom")
        mp_hull = rec["energy_above_hull"]
        chemsys = "-".join(sorted(rec["elements"]))

        print(f"  {mat_id} ({formula:12s}) mp_hull={mp_hull:.3f}", end="  ", flush=True)

        try:
            structure = _load_or_fetch_structure(mat_id, struct_cache)
        except Exception as exc:
            print(f"STRUCT_FAIL: {exc}")
            rows.append(_fail_row(rec, str(exc)))
            continue

        res = relax_structure(structure)
        if res["error"]:
            print(f"RELAX_FAIL: {res['error']}")
            rows.append(_fail_row(rec, res["error"], wall_ms=res["wall_ms"]))
            continue

        chgnet_epa = res["energy_per_atom"]
        relaxed = res["relaxed_structure"]

        # Leave-one-out hull (exclude this material_id from reference)
        hull_corr: float | None = None
        try:
            entries_corr = _load_or_fetch_entries(chemsys, hull_cache, corrected=True)
            ref_corr = _exclude_target(entries_corr, mat_id)
            hull_corr = e_above_hull(chgnet_epa, relaxed, ref_corr)
        except Exception as exc:
            print(f"HULL_CORR_FAIL:{exc}", end="  ")

        hull_uncorr: float | None = None
        try:
            entries_uncorr = _load_or_fetch_entries(chemsys, hull_cache, corrected=False)
            ref_uncorr = _exclude_target(entries_uncorr, mat_id)
            hull_uncorr = e_above_hull(chgnet_epa, relaxed, ref_uncorr)
        except Exception as exc:
            print(f"HULL_UNCORR_FAIL:{exc}", end="  ")

        print(f"chgnet_epa={chgnet_epa:.3f}  hull_c={hull_corr}  steps={res['n_steps']}")

        rows.append({
            "material_id": mat_id, "formula": formula, "chemsys": chemsys,
            "mp_energy_per_atom": mp_epa,
            "mp_uncorrected_energy_per_atom": mp_epa_uncorr,
            "mp_energy_above_hull": mp_hull,
            "chgnet_energy_per_atom": chgnet_epa,
            "chgnet_hull_corrected": hull_corr,
            "chgnet_hull_uncorrected": hull_uncorr,
            "n_steps": res["n_steps"], "wall_ms": res["wall_ms"], "error": None,
        })
        mat_ids_used.append(mat_id)

        record_result(run_id, None, 1, f"chgnet_epa:{mat_id}", chgnet_epa, path=args.ledger)
        if hull_corr is not None:
            record_result(run_id, None, 1, f"hull_corr:{mat_id}", hull_corr, path=args.ledger)

    t_end = _utc_iso()
    wall_ms_total = (time.time() - wall_t0) * 1000
    record_timing("stability_calibration", t_start, t_end, path=args.ledger)

    # ---- Metrics ----
    ok = [r for r in rows if r["error"] is None]
    n_ok, n_fail = len(ok), len(rows) - len(ok)
    if n_fail:
        failed_ids = [r["material_id"] for r in rows if r["error"]]
        print(f"\n  {n_fail} failed: {failed_ids}")
    if n_ok == 0:
        print("No successful relaxations. Exiting.")
        return

    mp_epa_arr    = np.array([r["mp_energy_per_atom"] for r in ok], dtype=float)
    mp_epa_u_arr  = np.array([r["mp_uncorrected_energy_per_atom"] or float("nan") for r in ok], dtype=float)
    mp_hull_arr   = np.array([r["mp_energy_above_hull"] for r in ok], dtype=float)
    chgnet_epa_arr = np.array([r["chgnet_energy_per_atom"] for r in ok], dtype=float)

    # (a) Energy/atom MAE
    mae_epa_corr   = float(np.nanmean(np.abs(chgnet_epa_arr - mp_epa_arr)))
    mae_epa_uncorr = float(np.nanmean(np.abs(chgnet_epa_arr - mp_epa_u_arr)))
    better_epa = "corrected" if mae_epa_corr <= mae_epa_uncorr else "uncorrected"

    # (b) Hull MAE
    def _hull_mae(key: str) -> float:
        pred = np.array([r[key] if r[key] is not None else float("nan") for r in ok], dtype=float)
        valid = ~np.isnan(pred)
        return float(np.mean(np.abs(pred[valid] - mp_hull_arr[valid]))) if valid.any() else float("nan")

    mae_hull_corr   = _hull_mae("chgnet_hull_corrected")
    mae_hull_uncorr = _hull_mae("chgnet_hull_uncorrected")

    valid_maes = [x for x in [mae_hull_corr, mae_hull_uncorr] if not np.isnan(x)]
    best_hull_mae = min(valid_maes) if valid_maes else float("nan")
    frozen_convention = "corrected" if (np.isnan(mae_hull_uncorr) or mae_hull_corr <= mae_hull_uncorr) else "uncorrected"

    # (c) Stability classification at 0.05 eV/atom
    THRESHOLD = 0.05
    pred_hull = np.array([
        r["chgnet_hull_corrected"] if r["chgnet_hull_corrected"] is not None
        else r["chgnet_hull_uncorrected"]
        for r in ok
    ], dtype=float)
    pred_pos = pred_hull <= THRESHOLD
    true_pos = mp_hull_arr <= THRESHOLD
    n_stable = int(true_pos.sum())

    tp = int((pred_pos & true_pos).sum())
    fp = int((pred_pos & ~true_pos).sum())
    tn = int((~pred_pos & ~true_pos).sum())
    fn = int((~pred_pos & true_pos).sum())
    precision = tp / (tp + fp) if (tp + fp) > 0 else float("nan")
    recall    = tp / (tp + fn) if (tp + fn) > 0 else float("nan")
    prec_ci   = _wilson_ci(tp, tp + fp)
    rec_ci    = _wilson_ci(tp, tp + fn)

    # Indeterminate recall if < 10 stable entries
    recall_indeterminate = n_stable < 10

    # Verdict
    if recall_indeterminate:
        usable = best_hull_mae <= 0.06  # recall criterion skipped
        verdict_recall = "INDETERMINATE (n_stable < 10)"
    else:
        usable = best_hull_mae <= 0.06 and recall >= 0.80
        verdict_recall = f"{recall:.3f}"
    verdict = "USABLE" if usable else "NOT USABLE AS-IS"

    # ---- Ledger summary ----
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

    # ---- Write reports ----
    REPORTS_DIR.mkdir(exist_ok=True)
    report = {
        "run_id": run_id,
        "addendum_sha256": addendum_sha,
        "n_selected": len(selected),
        "n_successful": n_ok,
        "n_failed": n_fail,
        "material_ids_used": mat_ids_used,
        "metrics": {
            "a_energy_per_atom_mae": {
                "vs_corrected_eV_per_atom": mae_epa_corr,
                "vs_uncorrected_eV_per_atom": mae_epa_uncorr,
                "better_agreement": better_epa,
            },
            "b_hull_mae": {
                "corrected_ref_eV_per_atom": mae_hull_corr,
                "uncorrected_ref_eV_per_atom": mae_hull_uncorr,
                "frozen_convention": frozen_convention,
            },
            "c_stability_classification_at_0.05": {
                "n_stable_mp": n_stable,
                "tp": tp, "fp": fp, "tn": tn, "fn": fn,
                "precision": precision,
                "precision_wilson_ci_95": prec_ci,
                "recall": recall,
                "recall_wilson_ci_95": rec_ci,
                "recall_indeterminate": recall_indeterminate,
            },
        },
        "verdict": {
            "usable": usable,
            "message": verdict,
            "best_hull_mae": best_hull_mae,
            "frozen_convention": frozen_convention,
            "recall": recall,
            "recall_indeterminate": recall_indeterminate,
            "thresholds": {"hull_mae": 0.06, "recall": 0.80},
        },
        "wall_ms_total": wall_ms_total,
    }
    json_path = REPORTS_DIR / "stability_calibration.json"
    csv_path  = REPORTS_DIR / "stability_calibration.csv"
    with open(json_path, "w") as f:
        json.dump(report, f, indent=2)
    pd.DataFrame(rows).to_csv(csv_path, index=False)

    # ---- Print compact table ----
    print(f"\n{'='*64}")
    print(f"  Stability Calibration  run_id={run_id}  n={n_ok}/{len(selected)}")
    print(f"{'='*64}")
    print(f"  (a) Energy/atom MAE vs corrected MP:   {mae_epa_corr:.4f} eV/atom")
    print(f"      Energy/atom MAE vs uncorrected MP:  {mae_epa_uncorr:.4f} eV/atom")
    print(f"      Better agreement: {better_epa}")
    print(f"  (b) Hull MAE (corrected ref):   {mae_hull_corr:.4f} eV/atom")
    print(f"      Hull MAE (uncorrected ref):  {mae_hull_uncorr:.4f} eV/atom")
    print(f"      *** FROZEN convention: {frozen_convention} (lower hull MAE) ***")
    print(f"  (c) n_stable(MP)={n_stable}  @ threshold 0.05 eV/atom:")
    print(f"      precision={precision:.3f}  Wilson95=[{prec_ci[0]:.3f},{prec_ci[1]:.3f}]")
    if recall_indeterminate:
        print(f"      recall=INDETERMINATE (n_stable={n_stable} < 10)")
    else:
        print(f"      recall={recall:.3f}  Wilson95=[{rec_ci[0]:.3f},{rec_ci[1]:.3f}]")
    print(f"      Confusion: TP={tp}  FP={fp}  TN={tn}  FN={fn}")
    print(f"\n  Verdict: {verdict}")
    if not usable:
        print(f"  (hull_MAE={best_hull_mae:.4f}, recall={verdict_recall})")
    print(f"  Reports: {json_path}, {csv_path}")
    print(f"{'='*64}\n")


def _fail_row(rec: dict, error: str, wall_ms: float | None = None) -> dict:
    return {
        "material_id": rec["material_id"], "formula": rec["formula"],
        "chemsys": "-".join(sorted(rec["elements"])),
        "mp_energy_per_atom": rec["energy_per_atom"],
        "mp_uncorrected_energy_per_atom": rec.get("uncorrected_energy_per_atom"),
        "mp_energy_above_hull": rec["energy_above_hull"],
        "chgnet_energy_per_atom": None, "chgnet_hull_corrected": None,
        "chgnet_hull_uncorrected": None, "n_steps": None,
        "wall_ms": wall_ms, "error": error,
    }


if __name__ == "__main__":
    main()
