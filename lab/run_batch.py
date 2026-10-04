"""lab/run_batch.py — Deterministic batch-level stability screening and validation queue.

Public API
----------
CandidateStabilityResult  dataclass  Pre-computed CHGNet result + screening fields.
screen_batch(candidates, policy)      Apply calibrated policy; populate screening fields.
build_validation_queue(screened, budget=None)
                                      Rank RETAIN candidates for expensive validation.
retrospective_metrics(screened, gt_threshold=0.05)
                                      TP/FP/TN/FN vs MP ground truth (calibration only).
record_screening_to_ledger(run_id, screened, ledger_path)
                                      Write to existing ledger schema (no schema change).

Scientific constraints (enforced throughout)
--------------------------------------------
* CHGNet hull values are NOT recomputed here — they must be pre-supplied by the caller.
* No DFT is executed anywhere in this module.
* The validation queue is a RECOMMENDATION for future DFT or experimental testing.
* MP ground-truth labels (mp_hull_ev_per_atom) are used ONLY for retrospective
  calibration evaluation, never as a generalization or discovery claim.
* The screening cutoff is an operational triage parameter, NOT a replacement for
  the MP 0.05 eV/atom DFT ground-truth criterion.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from lab.decision import _validate_policy, screen


# ---------------------------------------------------------------------------
# Candidate result dataclass
# ---------------------------------------------------------------------------

@dataclass
class CandidateStabilityResult:
    """Container for one candidate's CHGNet stability result and screening decision.

    The CHGNet hull field (``chgnet_hull_ev_per_atom``) must be pre-computed
    by the caller (e.g. from ``lab.stability.e_above_hull``).
    This module does NOT run CHGNet.

    Screening fields are populated by ``screen_batch()``.

    ``mp_hull_ev_per_atom`` is optional; when present it is used ONLY for
    retrospective calibration evaluation — not as a discovery claim.
    """
    # Identity
    candidate_id: str          # material_id (MP) or arbitrary string
    formula: str               # reduced formula

    # Pre-computed CHGNet result — do NOT re-run CHGNet here
    chgnet_hull_ev_per_atom: float    # E_above_hull, frozen_convention from policy

    # MP DFT ground truth — optional, for retrospective evaluation only
    mp_hull_ev_per_atom: Optional[float] = None

    # Screening decision — populated by screen_batch()
    screening_decision: Optional[str] = None          # "RETAIN" | "DEPRIORITIZE"
    validation_priority: Optional[float] = None       # lower = higher urgency
    decision_reason: Optional[str] = None             # machine-readable reason
    screening_cutoff_ev_per_atom: Optional[float] = None
    policy_calibration_run_id: Optional[int] = None

    # Ledger reference — set if candidate is registered in the candidates table
    ledger_candidate_id: Optional[int] = None


# ---------------------------------------------------------------------------
# Batch screening
# ---------------------------------------------------------------------------

def screen_batch(
    candidates: list[CandidateStabilityResult],
    policy: dict,
) -> list[CandidateStabilityResult]:
    """Apply the calibrated screening policy to pre-computed CHGNet hull values.

    Returns a new list in the same order as *candidates* with all screening
    fields populated. CHGNet hull values are NOT recomputed.

    Parameters
    ----------
    candidates :
        Each must have ``chgnet_hull_ev_per_atom`` already set.
    policy :
        Loaded and validated policy dict (from ``decision.load_policy()``).

    Returns
    -------
    list[CandidateStabilityResult]
        Screening fields populated: ``screening_decision``,
        ``validation_priority``, ``decision_reason``,
        ``screening_cutoff_ev_per_atom``, ``policy_calibration_run_id``.
    """
    _validate_policy(policy)
    cutoff = policy["screening_cutoff_ev_per_atom"]
    cal_run_id = policy.get("calibration_run_id")

    out: list[CandidateStabilityResult] = []
    for c in candidates:
        d = screen(c.chgnet_hull_ev_per_atom, policy)
        decision = d["decision"]
        reason = (
            "below_calibrated_chgnet_screening_cutoff"
            if decision == "RETAIN"
            else "above_calibrated_chgnet_screening_cutoff"
        )
        out.append(CandidateStabilityResult(
            candidate_id=c.candidate_id,
            formula=c.formula,
            chgnet_hull_ev_per_atom=c.chgnet_hull_ev_per_atom,
            mp_hull_ev_per_atom=c.mp_hull_ev_per_atom,
            ledger_candidate_id=c.ledger_candidate_id,
            screening_decision=decision,
            # Validation priority: lower hull → closer to convex hull → process first
            validation_priority=c.chgnet_hull_ev_per_atom,
            decision_reason=reason,
            screening_cutoff_ev_per_atom=cutoff,
            policy_calibration_run_id=cal_run_id,
        ))
    return out


# ---------------------------------------------------------------------------
# Validation queue
# ---------------------------------------------------------------------------

def build_validation_queue(
    screened: list[CandidateStabilityResult],
    budget: Optional[int] = None,
) -> list[CandidateStabilityResult]:
    """Return RETAIN candidates ranked for expensive validation.

    'Expensive validation' means future DFT computation or experimental
    measurement. No DFT is executed here. The queue is a deterministic
    recommendation only.

    Ranking rule (deterministic):
        Lower ``validation_priority`` (= lower CHGNet hull) means closer to
        the convex hull, i.e., more likely to pass a DFT stability check.
        Ties are broken by ``candidate_id`` (lexicographic) for reproducibility.

    Parameters
    ----------
    screened :
        Output of ``screen_batch()`` — ``screening_decision`` must be set.
    budget :
        If provided, return at most this many candidates.

    Returns
    -------
    RETAIN candidates in priority order (index 0 = most urgent for validation).
    """
    retained = [c for c in screened if c.screening_decision == "RETAIN"]
    retained.sort(key=lambda c: (c.validation_priority, c.candidate_id))
    if budget is not None:
        retained = retained[:budget]
    return retained


# ---------------------------------------------------------------------------
# Retrospective calibration evaluation
# ---------------------------------------------------------------------------

def retrospective_metrics(
    screened: list[CandidateStabilityResult],
    gt_threshold: float = 0.05,
) -> dict:
    """Compute TP/FP/TN/FN vs MP GGA/GGA+U ground truth.

    .. warning::

        These are **retrospective calibration metrics only**.
        The policy screening cutoff was selected from this same calibration dataset.
        These numbers do NOT represent generalisation to unseen materials and
        must not be reported as prospective performance.

    Parameters
    ----------
    screened :
        Candidates with ``screening_decision`` set.
        Only candidates with ``mp_hull_ev_per_atom`` not None contribute to
        the TP/FP/TN/FN counts.
    gt_threshold :
        MP GGA/GGA+U hull threshold for "stable". Default 0.05 eV/atom.

    Returns
    -------
    dict with ``label="retrospective_calibration_metrics"`` and a ``warning`` key.
    """
    total = len(screened)
    n_retained = sum(1 for c in screened if c.screening_decision == "RETAIN")
    n_deprioritized = total - n_retained
    retention_fraction = n_retained / total if total > 0 else math.nan

    with_gt = [
        c for c in screened
        if c.mp_hull_ev_per_atom is not None and c.screening_decision is not None
    ]
    n_evaluated = len(with_gt)

    tp = sum(
        1 for c in with_gt
        if c.screening_decision == "RETAIN" and c.mp_hull_ev_per_atom <= gt_threshold
    )
    fp = sum(
        1 for c in with_gt
        if c.screening_decision == "RETAIN" and c.mp_hull_ev_per_atom > gt_threshold
    )
    fn = sum(
        1 for c in with_gt
        if c.screening_decision == "DEPRIORITIZE" and c.mp_hull_ev_per_atom <= gt_threshold
    )
    tn = sum(
        1 for c in with_gt
        if c.screening_decision == "DEPRIORITIZE" and c.mp_hull_ev_per_atom > gt_threshold
    )
    precision = tp / (tp + fp) if (tp + fp) > 0 else math.nan
    recall = tp / (tp + fn) if (tp + fn) > 0 else math.nan

    return {
        "label": "retrospective_calibration_metrics",
        "warning": (
            "Policy cutoff was selected from this same calibration dataset. "
            "These metrics do NOT represent generalisation to unseen materials."
        ),
        "total_candidates": total,
        "n_with_ground_truth": n_evaluated,
        "n_retained": n_retained,
        "n_deprioritized": n_deprioritized,
        "retention_fraction": retention_fraction,
        "gt_threshold_ev_per_atom": gt_threshold,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "retrospective_precision": precision,
        "retrospective_recall": recall,
    }


# ---------------------------------------------------------------------------
# Ledger recording (no schema change)
# ---------------------------------------------------------------------------

def record_screening_to_ledger(
    run_id: int,
    screened: list[CandidateStabilityResult],
    ledger_path: Path,
) -> None:
    """Write CHGNet hull and screening decisions to the existing ledger schema.

    Uses the existing ``results`` table with no schema changes.
    Two rows are written per candidate:

    =========================================  =====  ==================
    metric                                     tier   value
    =========================================  =====  ==================
    ``chgnet_hull_ev_per_atom``                1      hull (float)
    ``screening_retain``                       1      1.0 (RETAIN) / 0.0
    =========================================  =====  ==================

    ``candidate_id`` is the ledger integer ID (``candidates.id``) when
    available, else ``None`` (allowed by the schema).
    """
    from lab import ledger as _ledger  # lazy — avoids pulling in sqlite3 at import

    for c in screened:
        if c.screening_decision is None:
            continue
        lid = c.ledger_candidate_id  # int or None
        _ledger.record_result(
            run_id=run_id,
            candidate_id=lid,
            tier=1,
            metric="chgnet_hull_ev_per_atom",
            value=c.chgnet_hull_ev_per_atom,
            path=ledger_path,
        )
        _ledger.record_result(
            run_id=run_id,
            candidate_id=lid,
            tier=1,
            metric="screening_retain",
            value=1.0 if c.screening_decision == "RETAIN" else 0.0,
            path=ledger_path,
        )
