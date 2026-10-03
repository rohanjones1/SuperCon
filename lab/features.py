"""lab/features.py — Pre-registered composition-only features (Arm A, prereg_001.yaml).

9 element properties × 5 statistics + nelements = 46 features.
No structure features. No element one-hots.
Imputation: train-fold medians only, fit inside CV loop.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from pymatgen.core import Composition, Element

# Pre-registered property list (fixed by prereg_001.yaml model.features)
_PROPS: list[str] = [
    "Z",            # atomic number
    "X",            # Pauling electronegativity
    "atomic_mass",
    "atomic_radius",
    "row",          # periodic table row
    "group",        # periodic table group
    "mendeleev_no",
    "melting_point",
    "molar_volume",
]

_STATS: list[str] = ["mean", "min", "max", "range", "std"]

FEATURE_NAMES: list[str] = [f"{p}_{s}" for p in _PROPS for s in _STATS] + ["nelements"]
# Total: 9 × 5 + 1 = 46


def _get_prop(el: Element, prop: str) -> float | None:
    """Return scalar element property as float, or None if unavailable."""
    try:
        val = getattr(el, prop)
        return None if val is None else float(val)
    except Exception:
        return None


def composition_features(formula: str) -> dict[str, float]:
    """Compute pre-registered features for one formula. Missing props -> NaN."""
    try:
        comp = Composition(formula)
    except Exception:
        return {name: float("nan") for name in FEATURE_NAMES}

    total = comp.num_atoms
    elements = list(comp.elements)
    fracs = [float(comp[el]) / total for el in elements]

    feat: dict[str, float] = {}
    for prop in _PROPS:
        raw = [_get_prop(el, prop) for el in elements]
        valid = [(v, f) for v, f in zip(raw, fracs) if v is not None]

        if not valid:
            for stat in _STATS:
                feat[f"{prop}_{stat}"] = float("nan")
            continue

        vals = [v for v, _ in valid]
        wfracs = [f for _, f in valid]
        frac_sum = sum(wfracs)

        wmean = sum(v * f for v, f in zip(vals, wfracs)) / frac_sum
        vmin = min(vals)
        vmax = max(vals)

        feat[f"{prop}_mean"] = wmean
        feat[f"{prop}_min"] = vmin
        feat[f"{prop}_max"] = vmax
        feat[f"{prop}_range"] = vmax - vmin
        feat[f"{prop}_std"] = float(np.std(vals))  # population std, unweighted

    feat["nelements"] = float(len(elements))
    return feat


def build_feature_matrix(formulas: pd.Series) -> pd.DataFrame:
    """Compute features for all formulas. Prints which properties had NaNs."""
    rows = [composition_features(f) for f in formulas]
    X = pd.DataFrame(rows, columns=FEATURE_NAMES, index=formulas.index)

    nan_cols = X.columns[X.isna().any()].tolist()
    if nan_cols:
        print(f"  Features with NaNs ({len(nan_cols)}): {nan_cols}")
    else:
        print("  No NaN features.")
    return X


def impute_with_train_medians(
    X_train: pd.DataFrame,
    X_test: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fill NaNs in both splits using medians from X_train only."""
    medians = X_train.median()
    return X_train.fillna(medians), X_test.fillna(medians)
