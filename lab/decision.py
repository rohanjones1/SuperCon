"""lab/decision.py — Deterministic CHGNet stability screening decisions.

Public API
----------
load_policy(path)         Load and validate a stability_policy.json.
screen(chgnet_hull, policy) -> dict   Return RETAIN / DEPRIORITIZE decision.

Scientific note
---------------
The CHGNet screening cutoff is an OPERATIONAL triage parameter calibrated
against the MP GGA/GGA+U hull.  It is NOT a replacement for DFT validation.
A RETAIN decision means "worth computing DFT stability", not "confirmed stable".
The DFT ground-truth threshold (0.05 eV/atom MP hull) is stored in the policy
for traceability but must never be changed by tuning the screening cutoff.
"""
from __future__ import annotations

import json
from pathlib import Path

_REQUIRED_KEYS: frozenset[str] = frozenset({
    "screening_cutoff_ev_per_atom",
    "ground_truth_threshold_ev_per_atom",
    "frozen_convention",
    "calibration_run_id",
    "recall_at_cutoff",
    "precision_at_cutoff",
})

_GROUND_TRUTH_THRESHOLD: float = 0.05  # MP GGA/GGA+U convention — not negotiable


def load_policy(path: str | Path) -> dict:
    """Load stability_policy.json from *path* and validate it.

    Raises
    ------
    FileNotFoundError  if the file does not exist.
    ValueError         if required keys are missing or ground_truth_threshold != 0.05.
    """
    policy = json.loads(Path(path).read_text())
    _validate_policy(policy)
    return policy


def _validate_policy(policy: dict) -> None:
    """Raise ValueError if *policy* is structurally invalid."""
    missing = _REQUIRED_KEYS - set(policy)
    if missing:
        raise ValueError(f"Policy missing required keys: {sorted(missing)}")
    gt = policy["ground_truth_threshold_ev_per_atom"]
    if abs(gt - _GROUND_TRUTH_THRESHOLD) > 1e-9:
        raise ValueError(
            f"ground_truth_threshold_ev_per_atom must be {_GROUND_TRUTH_THRESHOLD} "
            f"(MP GGA/GGA+U convention), got {gt}. "
            "Do not change the DFT definition — adjust screening_cutoff_ev_per_atom instead."
        )


def screen(chgnet_hull: float, policy: dict) -> dict:
    """Return a screening decision for a single candidate.

    Parameters
    ----------
    chgnet_hull : float
        CHGNet energy above hull (eV/atom), using the convention recorded
        in ``policy["frozen_convention"]``.
    policy : dict
        Loaded and validated policy dict (from ``load_policy``).

    Returns
    -------
    dict with keys:
        decision                    : "RETAIN" | "DEPRIORITIZE"
        chgnet_hull_ev_per_atom     : float   (echo of input)
        screening_cutoff_ev_per_atom: float   (from policy)
        note                        : str     (explicit CHGNet-vs-DFT distinction)
    """
    _validate_policy(policy)
    cutoff = policy["screening_cutoff_ev_per_atom"]
    gt_threshold = policy["ground_truth_threshold_ev_per_atom"]
    decision = "RETAIN" if chgnet_hull <= cutoff else "DEPRIORITIZE"
    return {
        "decision": decision,
        "chgnet_hull_ev_per_atom": chgnet_hull,
        "screening_cutoff_ev_per_atom": cutoff,
        "note": (
            f"CHGNet triage only — not a DFT stability validation. "
            f"RETAIN means: candidate is worth computing DFT stability. "
            f"DFT ground-truth threshold is {gt_threshold} eV/atom "
            f"(MP GGA/GGA+U hull, frozen_convention='{policy['frozen_convention']}')."
        ),
    }
