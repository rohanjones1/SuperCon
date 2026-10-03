"""tests/test_data.py — M2: split and leakage tests. No JARVIS download needed."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from lab.data import (
    assert_fold_integrity,
    assert_no_leakage,
    assign_folds,
    build_group_ids,
    split_by_group,
)


def _make_df(n: int = 60, n_groups: int = 12, seed: int = 0) -> pd.DataFrame:
    """Synthetic DataFrame with prototype_key and unique-per-row formula."""
    rng = np.random.default_rng(seed)
    groups = [f"G{i:02d}" for i in range(n_groups)]
    proto_keys = rng.choice(groups, size=n)
    return pd.DataFrame({
        "jid": [f"JVASP-{i}" for i in range(n)],
        "prototype_key": proto_keys,
        # Unique formulas so formula-leakage check never fires in clean splits
        "formula": [f"Elem{i}C" for i in range(n)],
        "ref_tc": rng.uniform(0.0, 30.0, size=n),
    })


# ── Existing split tests (updated for row-based stopping) ────────────────────

def test_no_group_in_both_splits():
    df = _make_df()
    df = split_by_group(df, "prototype_key", holdout_frac=0.25, seed=42)
    train_groups = set(df.loc[df["split"] == "train", "prototype_key"])
    holdout_groups = set(df.loc[df["split"] == "holdout", "prototype_key"])
    assert train_groups.isdisjoint(holdout_groups), (
        f"Groups in both splits: {train_groups & holdout_groups}"
    )


def test_split_deterministic():
    df = _make_df()
    df1 = split_by_group(df, "prototype_key", holdout_frac=0.25, seed=42)
    df2 = split_by_group(df, "prototype_key", holdout_frac=0.25, seed=42)
    assert list(df1["split"]) == list(df2["split"])


def test_split_respects_holdout_frac():
    """Holdout rows must reach (>=) the target row fraction."""
    df = _make_df(n=120, n_groups=20)
    df = split_by_group(df, "prototype_key", holdout_frac=0.25, seed=42)
    holdout_rows = int((df["split"] == "holdout").sum())
    assert holdout_rows >= int(0.25 * len(df)), (
        f"holdout rows {holdout_rows} < target {0.25 * len(df):.0f}"
    )


def test_whole_groups_assigned_consistently():
    """All rows with the same prototype_key must get the same split label."""
    df = _make_df()
    df = split_by_group(df, "prototype_key", holdout_frac=0.25, seed=42)
    for key, grp in df.groupby("prototype_key"):
        splits = grp["split"].unique()
        assert len(splits) == 1, f"Group {key} has rows in multiple splits: {splits}"


def test_assert_no_leakage_raises():
    """G0 appears in both splits via prototype_key -> leakage."""
    df = pd.DataFrame({
        "prototype_key": ["G0", "G0", "G1", "G2"],
        "formula":       ["AB",  "AB",  "CD", "EF"],
        "split":         ["train", "holdout", "train", "holdout"],
    })
    with pytest.raises(AssertionError, match="Leakage"):
        assert_no_leakage(df)


def test_assert_no_leakage_passes():
    df = _make_df()
    df = split_by_group(df, "prototype_key", holdout_frac=0.25, seed=42)
    assert_no_leakage(df)  # must not raise


# ── New M2c tests ─────────────────────────────────────────────────────────────

# (a) Same formula + different spacegroup -> same split (union-find)
def test_same_formula_different_spacegroup_same_split():
    """Rows sharing formula but differing spacegroup must land in the same split."""
    df = pd.DataFrame({
        "jid":           ["A",     "B",     "C",    "D",     "E"],
        "formula":       ["NbC",   "NbC",   "FeB",  "MgO",   "CaF2"],
        "prototype_key": ["AB_225","AB_221","AB_62","AB_225","AB2_225"],
        "ref_tc":        [5.0,     3.0,     1.0,    0.5,     7.0],
    })
    df["group_id"] = build_group_ids(df)
    # NbC entries share formula -> same group_id
    gids_nbc = df[df["formula"] == "NbC"]["group_id"].unique()
    assert len(gids_nbc) == 1, f"NbC has multiple group_ids: {gids_nbc}"
    # After split, both NbC rows go to the same side
    df = split_by_group(df, "group_id", holdout_frac=0.4, seed=42)
    splits_nbc = df[df["formula"] == "NbC"]["split"].unique()
    assert len(splits_nbc) == 1, f"NbC split across both splits: {splits_nbc}"


# (b) Holdout fraction reached without splitting any group
def test_holdout_fraction_row_based_no_group_split():
    """Row-based stopping: holdout rows >= target AND no group is split."""
    n = 120
    df = _make_df(n=n, n_groups=20)
    df = split_by_group(df, "prototype_key", holdout_frac=0.25, seed=42)
    holdout_rows = int((df["split"] == "holdout").sum())
    # Row target reached
    assert holdout_rows >= int(0.25 * n), (
        f"holdout rows {holdout_rows} < target {0.25 * n}"
    )
    # No group is split across both sides
    for key, grp in df.groupby("prototype_key"):
        assert grp["split"].nunique() == 1, (
            f"Group {key} was split across train and holdout"
        )


# ── New M2d fold tests ────────────────────────────────────────────────────────

def _make_grouped_df(n: int = 60, n_groups: int = 12, seed: int = 0) -> pd.DataFrame:
    df = _make_df(n=n, n_groups=n_groups, seed=seed)
    df["group_id"] = build_group_ids(df)
    return df


# (a) fold assignment is identical on repeated calls
def test_fold_assignment_deterministic():
    df = _make_grouped_df()
    df1 = assign_folds(df.copy())
    df2 = assign_folds(df.copy())
    assert list(df1["fold"]) == list(df2["fold"])


# (b) every group is in exactly one fold
def test_fold_each_group_in_one_fold():
    df = _make_grouped_df()
    df = assign_folds(df)
    for gid, grp in df.groupby("group_id"):
        folds = grp["fold"].unique()
        assert len(folds) == 1, f"group_id {gid} appears in folds {folds}"


# (c) max fold size - min fold size <= size of the largest group
def test_fold_balance_bounded_by_largest_group():
    df = _make_grouped_df(n=200, n_groups=30)
    df = assign_folds(df, n_folds=5)
    fold_sizes = df.groupby("fold").size()
    largest_group_size = int(df.groupby("group_id").size().max())
    imbalance = int(fold_sizes.max() - fold_sizes.min())
    assert imbalance <= largest_group_size, (
        f"imbalance {imbalance} > largest group {largest_group_size}"
    )


# (d) assert_fold_integrity raises when a formula spans two folds
def test_assert_fold_integrity_raises_on_formula_span():
    # Clean case: no spanning -> should pass
    df_ok = pd.DataFrame({
        "group_id":     [0,       0,       1,     1],
        "prototype_key":["AB_225","AB_225","CD_1","CD_1"],
        "formula":      ["NbC",   "NbC",   "FeB", "FeB"],
        "fold":         [0,       0,       1,     1],
    })
    assert_fold_integrity(df_ok)  # must not raise

    # Bad case: same formula in two different group_ids assigned to different folds
    df_bad = pd.DataFrame({
        "group_id":     [0,       1,       2,     3],
        "prototype_key":["AB_225","AB_221","CD_1","EF_1"],
        "formula":      ["NbC",   "NbC",   "FeB", "MgO"],
        "fold":         [0,       1,       2,     3],
    })
    with pytest.raises(AssertionError, match="Fold integrity"):
        assert_fold_integrity(df_bad)


# (c) assert_no_leakage raises on shared formula (even with different prototype_keys)
def test_assert_no_leakage_raises_on_shared_formula():
    """Shared formula across splits must trigger leakage even if prototype_keys differ."""
    df = pd.DataFrame({
        "prototype_key": ["AB_225", "AB_221", "CD_1",  "EF_1"],
        "formula":       ["NbC",    "NbC",    "FeB",   "MgO"],
        "split":         ["train",  "holdout","train", "holdout"],
    })
    with pytest.raises(AssertionError, match="Leakage"):
        assert_no_leakage(df)
