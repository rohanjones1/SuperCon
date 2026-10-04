"""tests/test_run_batch.py — 8 focused tests for lab/run_batch.py.

All tests are in-memory. No CHGNet, no DFT, no Materials Project network calls.
Test 8 uses a temporary SQLite file via the existing ledger helpers.
"""
from __future__ import annotations

import pytest

from lab.run_batch import (
    CandidateStabilityResult,
    build_validation_queue,
    record_screening_to_ledger,
    retrospective_metrics,
    screen_batch,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_POLICY = {
    "screening_cutoff_ev_per_atom": 0.0585,
    "ground_truth_threshold_ev_per_atom": 0.05,
    "frozen_convention": "corrected",
    "calibration_run_id": 3,
    "recall_at_cutoff": 0.80,
    "precision_at_cutoff": 0.75,
}


def _cand(cid: str, hull: float, mp_hull: float | None = None) -> CandidateStabilityResult:
    return CandidateStabilityResult(
        candidate_id=cid,
        formula=f"Formula_{cid}",
        chgnet_hull_ev_per_atom=hull,
        mp_hull_ev_per_atom=mp_hull,
    )


# ---------------------------------------------------------------------------
# 1. Batch results receive the screening decision after screen_batch()
# ---------------------------------------------------------------------------

def test_batch_results_receive_screening_decision():
    candidates = [_cand("mp-001", 0.02), _cand("mp-002", 0.10)]
    screened = screen_batch(candidates, _POLICY)

    assert len(screened) == 2
    for c in screened:
        assert c.screening_decision in ("RETAIN", "DEPRIORITIZE"), (
            f"{c.candidate_id}: unexpected decision {c.screening_decision!r}"
        )
        assert c.decision_reason is not None
        assert c.screening_cutoff_ev_per_atom == pytest.approx(0.0585)
        assert c.validation_priority is not None


# ---------------------------------------------------------------------------
# 2. Below (or at) cutoff → RETAIN with machine-readable reason
# ---------------------------------------------------------------------------

def test_below_cutoff_retain():
    [screened] = screen_batch([_cand("mp-A", 0.02)], _POLICY)
    assert screened.screening_decision == "RETAIN"
    assert screened.decision_reason == "below_calibrated_chgnet_screening_cutoff"

    # Exactly at cutoff is also RETAIN
    [at_cutoff] = screen_batch([_cand("mp-B", 0.0585)], _POLICY)
    assert at_cutoff.screening_decision == "RETAIN"


# ---------------------------------------------------------------------------
# 3. Above cutoff → DEPRIORITIZE
# ---------------------------------------------------------------------------

def test_above_cutoff_deprioritize():
    [screened] = screen_batch([_cand("mp-C", 0.20)], _POLICY)
    assert screened.screening_decision == "DEPRIORITIZE"
    assert screened.decision_reason == "above_calibrated_chgnet_screening_cutoff"


# ---------------------------------------------------------------------------
# 4. Validation queue is deterministic — same input → identical order
# ---------------------------------------------------------------------------

def test_validation_queue_is_deterministic():
    candidates = [
        _cand("mp-Z", 0.05),
        _cand("mp-A", 0.01),
        _cand("mp-M", 0.03),
    ]
    screened = screen_batch(candidates, _POLICY)

    q1 = [c.candidate_id for c in build_validation_queue(screened)]
    q2 = [c.candidate_id for c in build_validation_queue(screened)]
    assert q1 == q2

    # Lower hull comes first (most urgent for validation)
    priorities = [c.validation_priority for c in build_validation_queue(screened)]
    assert priorities == sorted(priorities)


# ---------------------------------------------------------------------------
# 5. Budget limits queue length; all returned candidates are RETAIN
# ---------------------------------------------------------------------------

def test_budget_limits_queue_length():
    candidates = [_cand(f"mp-{i:03d}", float(i) * 0.005) for i in range(20)]
    screened = screen_batch(candidates, _POLICY)
    queue = build_validation_queue(screened, budget=4)

    assert len(queue) <= 4
    for c in queue:
        assert c.screening_decision == "RETAIN"


# ---------------------------------------------------------------------------
# 6. Deprioritized candidates are NOT presented as proven unstable
# ---------------------------------------------------------------------------

def test_deprioritized_not_proven_unstable():
    [screened] = screen_batch([_cand("mp-999", 0.25)], _POLICY)
    assert screened.screening_decision == "DEPRIORITIZE"

    reason = screened.decision_reason.lower()
    forbidden = {"proven", "confirmed", "dft stable", "guaranteed", "unstable"}
    for bad_word in forbidden:
        assert bad_word not in reason, (
            f"Decision reason must not use language implying DFT confirmation; "
            f"found {bad_word!r} in {reason!r}"
        )
    # Must be clear it's a screening/cutoff decision
    assert "cutoff" in reason or "screening" in reason


# ---------------------------------------------------------------------------
# 7. No DFT execution required — module works without CHGNet or QE
# ---------------------------------------------------------------------------

def test_no_dft_execution_required():
    """screen_batch and build_validation_queue must not import CHGNet or QE."""
    import sys

    dft_before = {m for m in sys.modules if "chgnet" in m or "quantum_espresso" in m}

    candidates = [_cand("mp-X", 0.04), _cand("mp-Y", 0.12)]
    screened = screen_batch(candidates, _POLICY)
    queue = build_validation_queue(screened, budget=1)

    dft_after = {m for m in sys.modules if "chgnet" in m or "quantum_espresso" in m}
    assert dft_after == dft_before, (
        f"DFT modules imported unexpectedly: {dft_after - dft_before}"
    )
    assert len(queue) == 1
    assert queue[0].screening_decision == "RETAIN"


# ---------------------------------------------------------------------------
# 8. Policy/calibration run info preserved; ledger round-trip
# ---------------------------------------------------------------------------

def test_policy_info_preserved_and_ledger_roundtrip(tmp_path):
    """Screening policy metadata is preserved; results land in the ledger."""
    import sqlite3

    from lab.ledger import init_db, record_run

    db = tmp_path / "test.sqlite"
    init_db(db)
    run_id = record_run(
        tool="chgnet_screening_batch",
        tool_version="0.1",
        model_checkpoint=None,
        seed=None,
        hardware=None,
        wall_ms=None,
        path=db,
    )

    candidates = [
        _cand("mp-S1", 0.02, mp_hull=0.01),   # RETAIN; gt-stable
        _cand("mp-S2", 0.20, mp_hull=0.25),   # DEPRIORITIZE; gt-unstable
    ]
    screened = screen_batch(candidates, _POLICY)

    # Policy info preserved on each result
    for c in screened:
        assert c.policy_calibration_run_id == 3
        assert c.screening_cutoff_ev_per_atom == pytest.approx(0.0585)

    # Retrospective metrics carry a warning label (not a generalization claim)
    metrics = retrospective_metrics(screened)
    assert metrics["label"] == "retrospective_calibration_metrics"
    assert "warning" in metrics
    assert metrics["tp"] == 1
    assert metrics["tn"] == 1
    assert metrics["fp"] == 0
    assert metrics["fn"] == 0

    # Ledger round-trip: no schema change required
    record_screening_to_ledger(run_id, screened, db)

    con = sqlite3.connect(db)
    rows = con.execute(
        "SELECT metric, value FROM results WHERE run_id = ?", (run_id,)
    ).fetchall()
    con.close()

    written_metrics = {r[0] for r in rows}
    assert "chgnet_hull_ev_per_atom" in written_metrics
    assert "screening_retain" in written_metrics

    # RETAIN → 1.0, DEPRIORITIZE → 0.0
    retain_values = {r[1] for r in rows if r[0] == "screening_retain"}
    assert 1.0 in retain_values
    assert 0.0 in retain_values
