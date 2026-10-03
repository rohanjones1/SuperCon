"""tests/test_benchmark.py — Unit tests for lab/benchmark.py (M3)."""
from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from lab.benchmark import (
    _auroc,
    _hit_rate_top_k_pct,
    _enrichment,
    _run_cv,
    verify_prereg,
)
from lab.features import build_feature_matrix, impute_with_train_medians


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_df_with_folds(n_per_fold: int = 20, n_folds: int = 5) -> pd.DataFrame:
    """Minimal seeds DataFrame with fold, group_id, label, ref_tc, formula."""
    n = n_per_fold * n_folds
    rng = np.random.default_rng(7)
    labels = np.zeros(n, dtype=int)
    labels[:20] = 1  # 20 positives in fold 0 and 1

    formulas = [
        "NbB2", "MgB2", "LaB6", "YB6", "TiB2",
        "VB2", "CrB2", "ZrB2", "HfB2", "TaB2",
        "MoB2", "WB2", "FeB", "CoB", "NiB",
        "CuB", "ZnB2", "AlB2", "SiB6", "GeB6",
    ] * n_folds

    folds = np.repeat(np.arange(n_folds), n_per_fold)

    return pd.DataFrame({
        "jid": [f"JVASP-{i}" for i in range(n)],
        "formula": formulas[:n],
        "group_id": np.arange(n),  # each row its own group (no leakage issue)
        "fold": folds,
        "label": labels,
        "ref_tc": rng.uniform(0, 20, size=n),
    })


# ---------------------------------------------------------------------------
# Test 1: perfect scorer gives AUROC 1.0
# ---------------------------------------------------------------------------

def test_auroc_perfect():
    labels = np.array([0, 0, 0, 1, 1, 1])
    # Scores that perfectly rank positives above negatives
    scores = np.array([0.1, 0.2, 0.3, 0.8, 0.9, 1.0])
    assert _auroc(scores, labels) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Test 2: random scorer AUROC near 0.5
# ---------------------------------------------------------------------------

def test_auroc_random():
    rng = np.random.default_rng(42)
    n = 500
    labels = np.array([0] * (n // 2) + [1] * (n // 2))
    scores = rng.uniform(0, 1, size=n)
    auroc = _auroc(scores, labels)
    # Random scorer should be within ±0.1 of 0.5 with high probability
    assert abs(auroc - 0.5) < 0.1


# ---------------------------------------------------------------------------
# Test 3: verify_prereg raises RuntimeError on wrong hash
# ---------------------------------------------------------------------------

def test_verify_prereg_mismatch():
    import yaml

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # Write a seeds.csv with known content
        seeds_path = tmp / "seeds.csv"
        seeds_path.write_text("jid,formula\nJ1,MgB2\n")

        # Compute real hash
        real_hash = hashlib.sha256(seeds_path.read_bytes()).hexdigest()
        wrong_hash = "a" * 64  # definitely wrong

        # Write prereg yaml with the wrong hash
        prereg_path = tmp / "prereg.yaml"
        prereg_content = {"dataset": {"sha256": wrong_hash}}
        with open(prereg_path, "w") as f:
            yaml.dump(prereg_content, f)

        with pytest.raises(RuntimeError, match="SHA256 mismatch"):
            verify_prereg(seeds_path, prereg_path)


def test_verify_prereg_correct():
    import yaml

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        seeds_path = tmp / "seeds.csv"
        seeds_path.write_text("jid,formula\nJ1,MgB2\n")
        real_hash = hashlib.sha256(seeds_path.read_bytes()).hexdigest()

        prereg_path = tmp / "prereg.yaml"
        with open(prereg_path, "w") as f:
            yaml.dump({"dataset": {"sha256": real_hash}}, f)

        # Should not raise
        verify_prereg(seeds_path, prereg_path)


# ---------------------------------------------------------------------------
# Test 4: features have no NaN after imputation
# ---------------------------------------------------------------------------

def test_no_nan_after_imputation():
    # Real formulas from the boride family; some will have missing props
    formulas = pd.Series([
        "MgB2", "NbB2", "LaB6", "YB6", "TiB2",
        "ZrB2", "HfB2", "VB2", "CrB2", "MoB2",
        "TaB2", "WB2", "FeB", "CoB", "NiB",
        "AlB2", "SiB6", "CaB6", "SrB6", "BaB6",
    ])
    X = build_feature_matrix(formulas)
    X_train = X.iloc[:12]
    X_test = X.iloc[12:]

    X_tr_imp, X_te_imp = impute_with_train_medians(X_train, X_test)

    assert not X_tr_imp.isnull().any().any(), "NaNs remain in train after imputation"
    assert not X_te_imp.isnull().any().any(), "NaNs remain in test after imputation"


# ---------------------------------------------------------------------------
# Additional metric sanity checks
# ---------------------------------------------------------------------------

def test_hit_rate_top_k_perfect():
    labels = np.array([1, 1, 1, 0, 0, 0, 0, 0, 0, 0])
    scores = np.array([0.9, 0.8, 0.7, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1])
    # Top 30% (3 rows) should be the 3 positives
    hr = _hit_rate_top_k_pct(scores, labels, pct=0.3)
    assert hr == pytest.approx(1.0)


def test_enrichment_above_one_for_good_ranker():
    rng = np.random.default_rng(0)
    n = 200
    labels = np.array([1] * 20 + [0] * 180)
    # Good ranker: positives get higher scores
    scores = np.concatenate([rng.uniform(0.7, 1.0, 20), rng.uniform(0.0, 0.5, 180)])
    enrich = _enrichment(scores, labels)
    assert enrich > 1.0, f"Expected enrichment > 1 for good ranker, got {enrich:.3f}"
