"""lab/benchmark.py — Arm A pre-registered benchmark (prereg_001.yaml).

Grouped 5-fold CV with RandomForestClassifier.
Cluster bootstrap CIs over group_id (1000 resamples, seed 0, percentile method).
Baselines: B0 (random, seed=0), B1 (1/mean_atomic_mass, no training).
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import roc_auc_score

from lab.features import impute_with_train_medians

# Pre-registered hyperparameters (fixed by prereg_001.yaml)
_RF_PARAMS: dict = dict(
    n_estimators=500,
    min_samples_leaf=3,
    class_weight="balanced_subsample",
    random_state=0,
)
_BOOTSTRAP_N = 1000
_BOOTSTRAP_SEED = 0
_TOP_K_PCT = 0.10


# ---------------------------------------------------------------------------
# Pre-reg integrity check
# ---------------------------------------------------------------------------

def verify_prereg(seeds_path: Path, prereg_path: Path) -> None:
    """Raise RuntimeError if seeds.csv SHA256 doesn't match prereg dataset.sha256."""
    data = Path(seeds_path).read_bytes()
    actual_sha = hashlib.sha256(data).hexdigest()

    with open(prereg_path) as f:
        prereg = yaml.safe_load(f)

    expected_sha = str(prereg.get("dataset", {}).get("sha256", ""))
    if actual_sha != expected_sha:
        raise RuntimeError(
            f"seeds.csv SHA256 mismatch.\n"
            f"  actual:   {actual_sha}\n"
            f"  prereg:   {expected_sha}\n"
            "Run scripts/build_dataset.py and scripts/lock_prereg.py first."
        )


# ---------------------------------------------------------------------------
# Metric helpers
# ---------------------------------------------------------------------------

def _auroc(scores: np.ndarray, labels: np.ndarray) -> float:
    """AUROC; returns 0.5 when only one class present."""
    if len(np.unique(labels)) < 2:
        return 0.5
    return float(roc_auc_score(labels, scores))


def _hit_rate_top_k_pct(
    scores: np.ndarray, labels: np.ndarray, pct: float = _TOP_K_PCT
) -> float:
    """Fraction of positives in the top pct% of rows ranked by score."""
    k = max(1, int(len(scores) * pct))
    top_idx = np.argsort(scores)[-k:]
    return float(labels[top_idx].sum() / k)


def _enrichment(
    scores: np.ndarray, labels: np.ndarray, pct: float = _TOP_K_PCT
) -> float:
    """hit_rate_top_k_pct / base_rate; NaN when base_rate == 0."""
    base_rate = float(labels.mean())
    if base_rate == 0:
        return float("nan")
    return _hit_rate_top_k_pct(scores, labels, pct) / base_rate


# ---------------------------------------------------------------------------
# Cluster bootstrap
# ---------------------------------------------------------------------------

def _cluster_bootstrap(
    scores_dict: dict[str, np.ndarray],
    labels: np.ndarray,
    group_ids: np.ndarray,
    n: int = _BOOTSTRAP_N,
    seed: int = _BOOTSTRAP_SEED,
) -> dict:
    """
    Cluster bootstrap CIs for each scorer plus paired proxy-B1 difference.

    Parameters
    ----------
    scores_dict : {"proxy": ..., "B0": ..., "B1": ...}
    labels      : binary outcome array (0/1), positional with scores
    group_ids   : integer group ID per row
    n           : number of bootstrap resamples
    seed        : RNG seed

    Returns
    -------
    dict with keys matching scores_dict plus "proxy_minus_B1":
        {key: {auroc_ci, hit_rate_ci, enrichment_ci}}
        proxy_minus_B1: {auroc_diff_ci, hit_diff_ci}
    """
    rng = np.random.default_rng(seed)
    unique_groups = np.unique(group_ids)

    boot: dict[str, dict[str, list[float]]] = {
        k: {"auroc": [], "hit": [], "enrich": []} for k in scores_dict
    }
    boot_diff_auroc: list[float] = []
    boot_diff_hit: list[float] = []

    for _ in range(n):
        sampled = rng.choice(unique_groups, size=len(unique_groups), replace=True)
        idx = np.concatenate([np.where(group_ids == g)[0] for g in sampled])
        lbl = labels[idx]
        if len(np.unique(lbl)) < 2:
            continue

        for key, sc in scores_dict.items():
            s = sc[idx]
            boot[key]["auroc"].append(_auroc(s, lbl))
            boot[key]["hit"].append(_hit_rate_top_k_pct(s, lbl))
            boot[key]["enrich"].append(_enrichment(s, lbl))

        if "proxy" in scores_dict and "B1" in scores_dict:
            boot_diff_auroc.append(boot["proxy"]["auroc"][-1] - boot["B1"]["auroc"][-1])
            boot_diff_hit.append(boot["proxy"]["hit"][-1] - boot["B1"]["hit"][-1])

    def _ci(vals: list[float]) -> tuple[float, float]:
        if not vals:
            return (float("nan"), float("nan"))
        a = np.array(vals)
        return (float(np.percentile(a, 2.5)), float(np.percentile(a, 97.5)))

    result: dict = {}
    for key in scores_dict:
        result[key] = {
            "auroc_ci": _ci(boot[key]["auroc"]),
            "hit_rate_ci": _ci(boot[key]["hit"]),
            "enrichment_ci": _ci(boot[key]["enrich"]),
        }
    result["proxy_minus_B1"] = {
        "auroc_diff_ci": _ci(boot_diff_auroc),
        "hit_diff_ci": _ci(boot_diff_hit),
    }
    return result


# ---------------------------------------------------------------------------
# Cross-validation
# ---------------------------------------------------------------------------

def _run_cv(
    df: pd.DataFrame,
    X: pd.DataFrame,
    label_col: str = "label",
) -> np.ndarray:
    """
    5-fold OOF predictions using df["fold"] column.

    df and X must share the same 0-based positional index (reset_index applied
    before calling).

    Returns
    -------
    oof : float array, shape (len(df),), predict_proba positive class.
    """
    oof = np.full(len(df), np.nan)
    folds = sorted(df["fold"].unique())

    for fold_i in folds:
        train_mask = (df["fold"] != fold_i).values
        test_mask = (df["fold"] == fold_i).values

        X_tr = X.iloc[train_mask]
        X_te = X.iloc[test_mask]
        X_tr, X_te = impute_with_train_medians(X_tr, X_te)

        y_tr = df.iloc[train_mask][label_col].values

        clf = RandomForestClassifier(**_RF_PARAMS)
        clf.fit(X_tr, y_tr)
        oof[test_mask] = clf.predict_proba(X_te)[:, 1]

    return oof


# ---------------------------------------------------------------------------
# Baseline B1
# ---------------------------------------------------------------------------

def _b1_scores(X: pd.DataFrame) -> np.ndarray:
    """B1 baseline: score = 1 / atomic_mass_mean (no training required)."""
    return (1.0 / X["atomic_mass_mean"].values).astype(float)


# ---------------------------------------------------------------------------
# Sensitivity helpers
# ---------------------------------------------------------------------------

def _loso_auroc(
    df: pd.DataFrame,
    X: pd.DataFrame,
    group_to_drop: int,
    label_col: str = "label",
) -> tuple[float, float, dict]:
    """
    Leave-one-group-out: drop group_to_drop from training AND evaluation.
    Returns (proxy_auroc, B1_auroc, bootstrap_ci_dict).
    """
    keep = (df["group_id"] != group_to_drop).values
    df_sub = df.iloc[keep].reset_index(drop=True)
    X_sub = X.iloc[keep].reset_index(drop=True)

    oof_sub = _run_cv(df_sub, X_sub, label_col)
    labels_sub = df_sub[label_col].values
    groups_sub = df_sub["group_id"].values
    b1_sub = _b1_scores(X_sub)

    ci = _cluster_bootstrap(
        {"proxy": oof_sub, "B1": b1_sub}, labels_sub, groups_sub
    )
    return _auroc(oof_sub, labels_sub), _auroc(b1_sub, labels_sub), ci


def _within_group_auroc(
    df: pd.DataFrame,
    oof_scores: np.ndarray,
    label_col: str = "label",
    min_rows: int = 20,
    min_pos: int = 3,
    min_neg: int = 3,
) -> dict[int, float]:
    """
    Per-group AUROC for groups meeting size/class criteria.

    df must have 0-based positional index matching oof_scores.
    """
    labels = df[label_col].values
    group_ids = df["group_id"].values
    results: dict[int, float] = {}

    for gid in np.unique(group_ids):
        mask = group_ids == gid
        lbl = labels[mask]
        sc = oof_scores[mask]
        if mask.sum() < min_rows:
            continue
        if lbl.sum() < min_pos:
            continue
        if (1 - lbl).sum() < min_neg:
            continue
        results[int(gid)] = _auroc(sc, lbl)

    return results


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_arm_a(
    df: pd.DataFrame,
    X: pd.DataFrame,
    label_col: str = "label",
    loso_groups: tuple[int, ...] = (43, 2, 29),
) -> tuple[dict, pd.DataFrame]:
    """
    Full Arm A benchmark per prereg_001.yaml.

    Parameters
    ----------
    df        : seeds DataFrame (must include fold, group_id, ref_tc, label_col)
    X         : feature matrix (46 columns), same row order as df
    label_col : binary outcome column name
    loso_groups : group IDs for leave-one-group-out sensitivity

    Returns
    -------
    results_dict : all metrics, CIs, verdicts, sensitivity analyses
    oof_df       : per-row DataFrame with proxy_score, B0_score, B1_score
    """
    # Normalize index so positional operations are unambiguous
    df = df.reset_index(drop=True)
    X = X.reset_index(drop=True)

    labels = df[label_col].values
    group_ids = df["group_id"].values
    n = len(df)

    # ---- Proxy: 5-fold OOF ----
    proxy_scores = _run_cv(df, X, label_col)

    # ---- B0: uniform random, seed=0 ----
    rng0 = np.random.default_rng(0)
    b0_scores = rng0.uniform(0.0, 1.0, size=n)

    # ---- B1: 1 / mean_atomic_mass ----
    b1_scores = _b1_scores(X)

    scores_dict = {"proxy": proxy_scores, "B0": b0_scores, "B1": b1_scores}

    # ---- Point estimates ----
    pt: dict[str, dict] = {}
    for key, sc in scores_dict.items():
        pt[key] = {
            "auroc": _auroc(sc, labels),
            "hit_rate_top10": _hit_rate_top_k_pct(sc, labels),
            "enrichment": _enrichment(sc, labels),
        }

    # ---- Cluster bootstrap CIs ----
    ci = _cluster_bootstrap(scores_dict, labels, group_ids)

    # ---- H1 verdict: proxy AUROC CI lower bound > 0.5 ----
    h1_ci_lo = ci["proxy"]["auroc_ci"][0]
    h1_verdict = "supported" if h1_ci_lo > 0.5 else "not_supported"

    # ---- H2 verdict: paired diff CI ----
    diff_ci_lo = ci["proxy_minus_B1"]["auroc_diff_ci"][0]
    proxy_pt = pt["proxy"]["auroc"]
    b1_pt = pt["B1"]["auroc"]
    if proxy_pt <= b1_pt:
        h2_verdict = "not_supported"
    elif diff_ci_lo > 0:
        h2_verdict = "supported"
    else:
        h2_verdict = "inconclusive"

    # ---- Sensitivity: leave-one-group-out ----
    loso: dict = {}
    for gid in loso_groups:
        if (group_ids == gid).any():
            p_a, b1_a, loso_ci = _loso_auroc(df, X, int(gid), label_col)
            loso[int(gid)] = {
                "proxy_auroc": p_a,
                "B1_auroc": b1_a,
                "proxy_auroc_ci": loso_ci["proxy"]["auroc_ci"],
            }

    # ---- Sensitivity: within-group AUROC ----
    wg = _within_group_auroc(df, proxy_scores, label_col)
    wg_aurocs = list(wg.values())
    within_group_summary = {
        "n_qualifying_groups": len(wg_aurocs),
        "median_auroc": float(np.median(wg_aurocs)) if wg_aurocs else float("nan"),
        "min_auroc": float(np.min(wg_aurocs)) if wg_aurocs else float("nan"),
        "max_auroc": float(np.max(wg_aurocs)) if wg_aurocs else float("nan"),
        "per_group": {str(k): v for k, v in wg.items()},
    }

    # ---- Sensitivity: exploratory threshold 10K ----
    sensitivity_10k: dict | None = None
    if "ref_tc" in df.columns:
        labels_10k = (df["ref_tc"] >= 10.0).astype(int).values
        proxy_10k = _run_cv(
            df.assign(label_10k=labels_10k),
            X,
            label_col="label_10k",
        )
        b1_10k = b1_scores  # same formula scores, different labels
        ci_10k = _cluster_bootstrap(
            {"proxy": proxy_10k, "B1": b1_10k}, labels_10k, group_ids
        )
        sensitivity_10k = {
            "threshold_K": 10.0,
            "n_positive": int(labels_10k.sum()),
            "proxy_auroc": _auroc(proxy_10k, labels_10k),
            "B1_auroc": _auroc(b1_10k, labels_10k),
            "proxy_auroc_ci": ci_10k["proxy"]["auroc_ci"],
        }

    results_dict: dict = {
        "n_samples": int(n),
        "n_positive": int(labels.sum()),
        "base_rate": float(labels.mean()),
        "point_estimates": pt,
        "bootstrap_ci": ci,
        "verdicts": {
            "H1": {
                "text": "proxy OOF AUROC CI lower bound > 0.5",
                "verdict": h1_verdict,
                "auroc": pt["proxy"]["auroc"],
                "auroc_ci": ci["proxy"]["auroc_ci"],
            },
            "H2": {
                "text": "proxy AUROC > B1 AUROC (paired diff CI lower bound > 0)",
                "verdict": h2_verdict,
                "proxy_auroc": proxy_pt,
                "B1_auroc": b1_pt,
                "diff_ci": ci["proxy_minus_B1"]["auroc_diff_ci"],
            },
        },
        "sensitivity": {
            "loso": loso,
            "within_group_auroc": within_group_summary,
            "exploratory_10k": sensitivity_10k,
        },
    }

    # ---- OOF output DataFrame ----
    keep_cols = [c for c in ["jid", "formula", "group_id", "fold", label_col, "ref_tc"] if c in df.columns]
    oof_df = df[keep_cols].copy()
    oof_df["proxy_score"] = proxy_scores
    oof_df["B0_score"] = b0_scores
    oof_df["B1_score"] = b1_scores

    return results_dict, oof_df
