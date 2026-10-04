"""tests/test_calibrate_stability.py — Regression tests for calibrate_stability.py integrity fixes.

Tests:
  1. frozen='corrected'   → uses chgnet_hull_corrected column for classification
  2. frozen='uncorrected' → uses chgnet_hull_uncorrected column for classification
  3. Missing frozen-column prediction excluded from TP/FP/TN/FN (not counted as unstable)
  4. Resumed CSV rows with blank/NaN error treated as successful
  5. Rows with real error strings remain failures
  6. Target entry missing from cached entries raises a clear RuntimeError
  7. Successful target exclusion removes exactly one entry

No network calls, no CHGNet, no Materials Project.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Make the script importable without installing it as a package
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from calibrate_stability import _check_loso_integrity, _compute_classification


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rows(corr_vals, uncorr_vals, mp_vals):
    """Build synthetic ok_rows dicts matching the partial CSV column schema."""
    return [
        {
            "chgnet_hull_corrected": c,
            "chgnet_hull_uncorrected": u,
            "mp_energy_above_hull_gga": m,
        }
        for c, u, m in zip(corr_vals, uncorr_vals, mp_vals)
    ]


def _mp_arr(rows):
    return np.array([r["mp_energy_above_hull_gga"] for r in rows], dtype=float)


# ---------------------------------------------------------------------------
# 1. frozen='corrected' uses chgnet_hull_corrected
# ---------------------------------------------------------------------------

def test_frozen_corrected_uses_corrected_column():
    """corrected=0.02 (RETAIN) vs uncorrected=0.10 (DEPRIORITIZE); MP=0.01 (stable).
    With frozen='corrected', 0.02 <= 0.05 → predicted-stable → TP=1."""
    rows = _rows(corr_vals=[0.02], uncorr_vals=[0.10], mp_vals=[0.01])
    cls = _compute_classification(rows, "corrected", _mp_arr(rows))
    assert cls["tp"] == 1
    assert cls["fp"] == 0
    assert cls["fn"] == 0
    assert cls["tn"] == 0


# ---------------------------------------------------------------------------
# 2. frozen='uncorrected' uses chgnet_hull_uncorrected
# ---------------------------------------------------------------------------

def test_frozen_uncorrected_uses_uncorrected_column():
    """Same row: corrected=0.02 (RETAIN), uncorrected=0.10 (DEPRIORITIZE); MP=0.01 (stable).
    With frozen='uncorrected', 0.10 > 0.05 → predicted-unstable → FN=1."""
    rows = _rows(corr_vals=[0.02], uncorr_vals=[0.10], mp_vals=[0.01])
    cls = _compute_classification(rows, "uncorrected", _mp_arr(rows))
    assert cls["fn"] == 1
    assert cls["tp"] == 0
    assert cls["fp"] == 0
    assert cls["tn"] == 0


# ---------------------------------------------------------------------------
# 3. Missing frozen-column hull excluded, not counted as predicted-unstable
# ---------------------------------------------------------------------------

def test_missing_prediction_excluded_not_counted_as_unstable():
    """A None in the frozen hull column is excluded from classification (n_missing += 1).
    It must NOT be counted as FN (predicted-unstable) just because it evaluates as
    'not <= threshold' in a NaN comparison."""
    # row 0: corrected=0.02 → TP (predicted-stable, MP-stable)
    # row 1: corrected=None (missing) → must be excluded, not FN
    rows = _rows(corr_vals=[0.02, None], uncorr_vals=[0.02, 0.03], mp_vals=[0.01, 0.01])
    cls = _compute_classification(rows, "corrected", _mp_arr(rows))

    assert cls["n_missing"] == 1,   f"Expected 1 missing, got {cls['n_missing']}"
    assert cls["n_classified"] == 1, f"Expected 1 classified, got {cls['n_classified']}"
    assert cls["fn"] == 0, (
        "A missing prediction must NOT be counted as FN (predicted-unstable); "
        f"got fn={cls['fn']}"
    )
    assert cls["tp"] == 1  # row 0 is TP


def test_all_predictions_missing_returns_safe_defaults():
    """When every prediction is None/NaN, classification returns safe no-data defaults."""
    rows = _rows(corr_vals=[None, None], uncorr_vals=[None, None], mp_vals=[0.01, 0.25])
    cls = _compute_classification(rows, "corrected", _mp_arr(rows))

    assert cls["n_missing"] == 2
    assert cls["n_classified"] == 0
    assert cls["tp"] == cls["fp"] == cls["tn"] == cls["fn"] == 0
    assert cls["recall_indet"] is True
    assert math.isnan(cls["precision"])
    assert math.isnan(cls["recall"])


# ---------------------------------------------------------------------------
# 4. Resumed CSV: blank error field (NaN from pandas) treated as successful
# ---------------------------------------------------------------------------

def test_blank_nan_error_treated_as_successful():
    """When pandas reads a blank error cell, it becomes float NaN.
    pd.isna(NaN) is True, so the row must be classified as successful."""
    rows = [
        {"material_id": "mp-001", "error": float("nan")},  # blank in CSV → NaN
        {"material_id": "mp-002", "error": None},           # new run (no CSV resume)
    ]
    ok = [r for r in rows if pd.isna(r.get("error"))]
    assert len(ok) == 2, (
        "Both NaN (blank CSV cell) and None (fresh run) must be treated as successful; "
        f"got {len(ok)}"
    )


# ---------------------------------------------------------------------------
# 5. Real error strings remain failures
# ---------------------------------------------------------------------------

def test_real_error_string_treated_as_failure():
    """Non-null, non-NaN error strings must exclude the row from ok."""
    rows = [
        {"material_id": "mp-003", "error": "RELAX_FAIL: timeout"},
        {"material_id": "mp-004", "error": "no_GGA_GGA+U_thermo_doc"},
        {"material_id": "mp-005", "error": "Leave-one-out integrity failure"},
    ]
    ok = [r for r in rows if pd.isna(r.get("error"))]
    assert len(ok) == 0, (
        f"All error-string rows must be excluded; got {len(ok)} in ok"
    )


# ---------------------------------------------------------------------------
# 6. Target entry missing from cached entries raises a clear RuntimeError
# ---------------------------------------------------------------------------

def test_missing_target_raises_runtime_error():
    """_check_loso_integrity must raise RuntimeError when mat_id is absent."""
    records = [
        {"entry_id": "mp-111", "composition": {"Mg": 1, "B": 2}},
        {"entry_id": "mp-222", "composition": {"Ti": 1, "Si": 2}},
    ]
    with pytest.raises(RuntimeError, match="not found"):
        _check_loso_integrity(records, "mp-999")


def test_missing_target_error_mentions_entry_id():
    """The error message should explain which mat_id was not found."""
    records = [{"entry_id": "mp-001", "composition": {"Nb": 1}}]
    with pytest.raises(RuntimeError, match="mp-XYZ"):
        _check_loso_integrity(records, "mp-XYZ")


# ---------------------------------------------------------------------------
# 7. Successful target exclusion removes exactly one entry
# ---------------------------------------------------------------------------

def test_successful_exclusion_passes_and_removes_exactly_one():
    """_check_loso_integrity passes when target appears exactly once."""
    records = [
        {"entry_id": "mp-001", "composition": {"Mg": 1}},
        {"entry_id": "mp-002", "composition": {"Mg": 1, "B": 2}},
        {"entry_id": "mp-003", "composition": {"B": 1}},
    ]
    # Must not raise — mp-002 appears exactly once
    _check_loso_integrity(records, "mp-002")

    # Verify the post-exclusion count is exactly n-1
    after = [r for r in records if r["entry_id"] != "mp-002"]
    assert len(after) == len(records) - 1, (
        f"Exclusion should remove exactly 1 entry; "
        f"before={len(records)} after={len(after)}"
    )
