#!/usr/bin/env python3
"""scripts/build_stability_policy.py — Sweep CHGNet hull cutoffs; select operational screening threshold.

Reads:
  reports/stability_calibration.csv   (per-compound CHGNet and MP hull values)
  reports/stability_calibration.json  (frozen_convention, calibration_run_id)

Writes:
  reports/stability_policy.json

Scientific note
---------------
The MP 0.05 eV/atom threshold is the DFT ground truth definition (from
addendum_stability_001.yaml). The CHGNet screening cutoff is a SEPARATE
operational parameter chosen to maximise recall (target >= 0.80) with
best precision over the calibration set. These must never be confused.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).parent.parent
CALIB_CSV = REPO_ROOT / "reports" / "stability_calibration.csv"
CALIB_JSON = REPO_ROOT / "reports" / "stability_calibration.json"
OUT_JSON = REPO_ROOT / "reports" / "stability_policy.json"

GT_THRESHOLD = 0.05   # MP GGA/GGA+U hull — ground truth, NOT a tunable parameter
GT_COLUMN = "mp_energy_above_hull_gga"
RECALL_TARGET = 0.80  # from addendum_stability_001.yaml usability criterion


def _wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denom
    half = z * (p * (1 - p) / n + z**2 / (4 * n**2)) ** 0.5 / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def main() -> None:
    calib = json.loads(CALIB_JSON.read_text())
    frozen_convention = calib["metrics"]["b_hull_mae"]["frozen_convention"]
    run_id = calib["run_id"]

    # float_precision="round_trip": parse floats exactly as Python float() does (lab/tools_api.py).
    # The pandas default C parser can differ in the last bits, which made the stored cutoff differ
    # from the boundary row's hull as read by lab.decision.screen() (see DECISIONS.md D22).
    df = pd.read_csv(CALIB_CSV, float_precision="round_trip")
    # Only rows with successful CHGNet evaluation
    df = df[df["error"].isna()].copy()

    # Ground truth: MP GGA/GGA+U hull <= GT_THRESHOLD
    df["gt_stable"] = df[GT_COLUMN] <= GT_THRESHOLD

    # CHGNet hull column determined by frozen convention
    chgnet_col = (
        "chgnet_hull_corrected" if frozen_convention == "corrected"
        else "chgnet_hull_uncorrected"
    )
    df["chgnet_hull"] = df[chgnet_col].astype(float)
    # Missing predictions are excluded, never counted as DEPRIORITIZE (same rule as lab.tools_api._load_cache).
    n_missing_predictions = int(df["chgnet_hull"].isna().sum())
    df = df[df["chgnet_hull"].notna()].copy()

    n_stable = int(df["gt_stable"].sum())
    n_unstable = len(df) - n_stable

    # Sweep all unique CHGNet hull values as candidate cutoffs
    cutoffs = sorted(df["chgnet_hull"].dropna().unique())

    sweep = []
    for cutoff in cutoffs:
        pred_stable = df["chgnet_hull"] <= cutoff
        tp = int((pred_stable & df["gt_stable"]).sum())
        fp = int((pred_stable & ~df["gt_stable"]).sum())
        fn = int((~pred_stable & df["gt_stable"]).sum())
        tn = int((~pred_stable & ~df["gt_stable"]).sum())
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / n_stable if n_stable > 0 else 0.0
        sweep.append({
            "cutoff": cutoff, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": precision, "recall": recall,
        })

    # Select: minimum cutoff achieving recall >= RECALL_TARGET
    # (lower cutoff = fewer false positives at the same recall)
    meets_target = [r for r in sweep if r["recall"] >= RECALL_TARGET]
    if meets_target:
        chosen = min(meets_target, key=lambda r: r["cutoff"])
        target_met = True
    else:
        # Fallback: highest available recall, break ties by lowest cutoff
        chosen = max(sweep, key=lambda r: (r["recall"], -r["cutoff"]))
        target_met = False

    cutoff_rows = df.loc[df["chgnet_hull"] == chosen["cutoff"], "material_id"].tolist()
    prec_ci = _wilson_ci(chosen["tp"], chosen["tp"] + chosen["fp"])
    rec_ci = _wilson_ci(chosen["tp"], n_stable)

    policy = {
        "screening_cutoff_ev_per_atom": chosen["cutoff"],
        "ground_truth_threshold_ev_per_atom": GT_THRESHOLD,
        "frozen_convention": frozen_convention,
        "calibration_run_id": run_id,
        "recall_at_cutoff": chosen["recall"],
        "precision_at_cutoff": chosen["precision"],
        "recall_wilson_ci_95": list(rec_ci),
        "precision_wilson_ci_95": list(prec_ci),
        "tp": chosen["tp"],
        "fp": chosen["fp"],
        "fn": chosen["fn"],
        "tn": chosen["tn"],
        "n_stable_gt": n_stable,
        "n_unstable_gt": n_unstable,
        "recall_target": RECALL_TARGET,
        "recall_target_met": target_met,
        "ground_truth_column": GT_COLUMN,
        "chgnet_hull_column": chgnet_col,
        "cutoff_source_material_ids": cutoff_rows,
        "n_rows_used": int(len(df)),
        "n_missing_predictions_excluded": n_missing_predictions,
        "csv_float_parsing": "round_trip (identical to Python float())",
        "selection_rule": "minimum CHGNet hull cutoff with in-sample recall >= recall_target",
        "note": (
            "screening_cutoff_ev_per_atom is the CHGNet operational triage threshold — "
            "a calibrated approximation to the MP hull. "
            "ground_truth_threshold_ev_per_atom (0.05 eV/atom) is the MP GGA/GGA+U DFT "
            "definition and is NOT replaced or approximated by this cutoff."
        ),
    }

    OUT_JSON.write_text(json.dumps(policy, indent=2))

    # Compact summary
    print(f"Frozen convention    : {frozen_convention}")
    print(f"Ground truth (DFT)   : mp_hull_gga <= {GT_THRESHOLD} eV/atom  (n_stable={n_stable})")
    print(f"Recall target        : >= {RECALL_TARGET}")
    print(f"Selected CHGNet cutoff: {chosen['cutoff']:.6f} eV/atom")
    print(f"  TP={chosen['tp']} FP={chosen['fp']} FN={chosen['fn']} TN={chosen['tn']}")
    print(f"  recall={chosen['recall']:.3f} [{rec_ci[0]:.3f}, {rec_ci[1]:.3f}]")
    print(f"  precision={chosen['precision']:.3f} [{prec_ci[0]:.3f}, {prec_ci[1]:.3f}]")
    print(f"  recall_target_met={target_met}")
    print(f"Policy written → {OUT_JSON.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
