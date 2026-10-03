"""tests/test_decision.py — 7 focused unit tests for lab/decision.py.

No file I/O — uses an in-memory _MOCK_POLICY dict throughout.
"""
from __future__ import annotations

import pytest

from lab.decision import _validate_policy, screen

_MOCK_POLICY = {
    "screening_cutoff_ev_per_atom": 0.0585,
    "ground_truth_threshold_ev_per_atom": 0.05,
    "frozen_convention": "corrected",
    "calibration_run_id": 3,
    "recall_at_cutoff": 0.80,
    "precision_at_cutoff": 0.75,
}


# ---------------------------------------------------------------------------
# 1. Valid policy passes validation without raising
# ---------------------------------------------------------------------------

def test_validate_policy_passes_for_valid_policy():
    _validate_policy(_MOCK_POLICY)  # must not raise


# ---------------------------------------------------------------------------
# 2. At or below cutoff → RETAIN
# ---------------------------------------------------------------------------

def test_at_or_below_cutoff_returns_retain():
    r_below = screen(0.02, _MOCK_POLICY)
    r_at = screen(0.0585, _MOCK_POLICY)  # exactly at cutoff
    assert r_below["decision"] == "RETAIN"
    assert r_at["decision"] == "RETAIN"


# ---------------------------------------------------------------------------
# 3. Above cutoff → DEPRIORITIZE
# ---------------------------------------------------------------------------

def test_above_cutoff_returns_deprioritize():
    result = screen(0.10, _MOCK_POLICY)
    assert result["decision"] == "DEPRIORITIZE"


# ---------------------------------------------------------------------------
# 4. MP 0.05 ground-truth threshold is preserved in policy and output note
# ---------------------------------------------------------------------------

def test_ground_truth_threshold_is_0_05():
    assert _MOCK_POLICY["ground_truth_threshold_ev_per_atom"] == pytest.approx(0.05)
    note = screen(0.03, _MOCK_POLICY)["note"]
    assert "0.05" in note, f"DFT threshold not mentioned in note: {note!r}"


# ---------------------------------------------------------------------------
# 5. Deterministic — identical inputs always produce identical outputs
# ---------------------------------------------------------------------------

def test_deterministic():
    r1 = screen(0.07, _MOCK_POLICY)
    r2 = screen(0.07, _MOCK_POLICY)
    assert r1 == r2


# ---------------------------------------------------------------------------
# 6. Output explicitly distinguishes CHGNet screening from DFT validation
# ---------------------------------------------------------------------------

def test_output_distinguishes_screening_from_dft():
    result = screen(0.03, _MOCK_POLICY)
    note = result["note"].lower()
    assert "dft" in note, "Output note must mention DFT to make distinction explicit"
    assert "screening_cutoff_ev_per_atom" in result
    assert result["chgnet_hull_ev_per_atom"] == pytest.approx(0.03)
    assert "chgnet" in note or "triage" in note, (
        "Note must clarify this is CHGNet triage, not a DFT result"
    )


# ---------------------------------------------------------------------------
# 7. Wrong ground_truth_threshold or missing key → clear ValueError
# ---------------------------------------------------------------------------

def test_invalid_policy_raises_clear_error():
    # Wrong ground_truth_threshold
    bad_gt = dict(_MOCK_POLICY, ground_truth_threshold_ev_per_atom=0.10)
    with pytest.raises(ValueError, match="ground_truth_threshold"):
        _validate_policy(bad_gt)

    # Missing required key
    bad_missing = {k: v for k, v in _MOCK_POLICY.items() if k != "calibration_run_id"}
    with pytest.raises(ValueError, match="missing required keys"):
        _validate_policy(bad_missing)
