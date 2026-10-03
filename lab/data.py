"""lab/data.py — JARVIS supercon_3d loader, splitter, leakage guard.

No Omnigent imports. Returns plain DataFrames and dicts.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from pymatgen.core import Lattice, Structure
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

DEFAULT_RAW = Path("data/raw")
_DATASET = "supercon_3d"
_FULL_CACHE = "jarvis_supercon_3d_full.json"


# ── Raw loading ──────────────────────────────────────────────────────────────

def _load_raw(raw_dir: Path) -> list[dict]:
    full_cache = raw_dir / _FULL_CACHE
    if full_cache.exists():
        print(f"[cache] {full_cache}")
        return json.loads(full_cache.read_text())
    print(f"Downloading JARVIS {_DATASET} ...")
    from jarvis.db.figshare import data as jdata
    records = jdata(_DATASET)
    full_cache.write_text(json.dumps(records, default=str))
    print(f"Cached {len(records)} records -> {full_cache}")
    return records


# ── Structure helpers ─────────────────────────────────────────────────────────

def _atoms_to_structure(atoms_dict: dict | str) -> Structure | None:
    """Convert JARVIS atoms dict to pymatgen Structure.

    Method: jarvis.core.atoms.Atoms.from_dict() -> pymatgen_converter(),
    with a direct lat_mat/coords fallback. Spacegroup is then derived via
    SpacegroupAnalyzer (symprec=0.1) and stored in prototype_key.
    """
    if isinstance(atoms_dict, str):
        try:
            atoms_dict = json.loads(atoms_dict)
        except Exception:
            return None
    try:
        from jarvis.core.atoms import Atoms as JAtoms
        return JAtoms.from_dict(atoms_dict).pymatgen_converter()
    except Exception:
        pass
    try:
        lat = atoms_dict.get("lat_mat") or atoms_dict.get("lattice_mat")
        elements = atoms_dict["elements"]
        coords = atoms_dict["coords"]
        cartesian = bool(atoms_dict.get("cartesian", False))
        return Structure(Lattice(lat), elements, coords, coords_are_cartesian=cartesian)
    except Exception:
        return None


def _spacegroup_number(struct: Structure) -> int | None:
    try:
        return SpacegroupAnalyzer(struct, symprec=0.1).get_space_group_number()
    except Exception:
        return None


# ── Group IDs via union-find ──────────────────────────────────────────────────

def build_group_ids(df: pd.DataFrame) -> pd.Series:
    """Union-find over prototype_key and formula.

    Rows sharing prototype_key OR sharing the same reduced formula are merged
    into the same connected component.  Returns a Series of integer group IDs
    (0-based, contiguous).  Used as the split unit to prevent composition leakage.
    """
    n = len(df)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]  # path compression
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # Merge by prototype_key
    proto_first: dict[str, int] = {}
    for i, pk in enumerate(df["prototype_key"]):
        if pk is None or (isinstance(pk, float) and np.isnan(pk)):
            continue
        pk = str(pk)
        if pk in proto_first:
            union(i, proto_first[pk])
        else:
            proto_first[pk] = i

    # Merge by reduced formula (catches polymorphs with same composition)
    formula_first: dict[str, int] = {}
    for i, fm in enumerate(df["formula"]):
        if fm is None or (isinstance(fm, float) and np.isnan(fm)):
            continue
        fm = str(fm)
        if fm in formula_first:
            union(i, formula_first[fm])
        else:
            formula_first[fm] = i

    # Canonicalise roots -> contiguous int IDs
    root_to_gid: dict[int, int] = {}
    gids: list[int] = []
    for i in range(n):
        root = find(i)
        if root not in root_to_gid:
            root_to_gid[root] = len(root_to_gid)
        gids.append(root_to_gid[root])

    return pd.Series(gids, index=df.index, dtype=int)


# ── Main loader ──────────────────────────────────────────────────────────────

def load_supercon(
    raw_dir: Path = DEFAULT_RAW,
) -> tuple[pd.DataFrame, dict[str, Structure | None]]:
    """Load and filter JARVIS supercon_3d.

    Returns
    -------
    df : DataFrame
        Filtered, annotated. Columns include prototype_key, group_id, ref_tc.
    structures : dict[jid -> pymatgen Structure | None]
        For writing structures.json in build_dataset.py.
    """
    raw_dir = Path(raw_dir)
    records = _load_raw(raw_dir)
    df = pd.DataFrame(records)
    print(f"Loaded             : {len(df):5d} records")

    # ── Filters ─────────────────────────────────────────────────────────────
    df = df[df["press"] == "0 GPa"].copy()
    print(f"After press=='0 GPa'       : {len(df):5d}")
    df = df[df["stability"] == "stable"].copy()
    print(f"After stability=='stable'  : {len(df):5d}")

    # ── Dedupe by jid ────────────────────────────────────────────────────────
    n_dup_jid = df.duplicated("jid").sum()
    if n_dup_jid:
        print(f"Dropping {n_dup_jid} duplicate jid rows")
    df = df.drop_duplicates("jid").reset_index(drop=True)

    # ── Structure features ────────────────────────────────────────────────────
    # Spacegroup derived from pymatgen SpacegroupAnalyzer (symprec=0.1)
    # because JARVIS supercon_3d has no spacegroup field.
    print("Deriving formula / spacegroup via pymatgen (SpacegroupAnalyzer symprec=0.1) ...")
    structures: dict[str, Structure | None] = {}
    formulas, formula_anons, spacegroups, elems_col, nelems_col = [], [], [], [], []
    n_failed = 0

    for i, (_, row) in enumerate(df.iterrows()):
        if i % 200 == 0:
            print(f"  {i}/{len(df)} ...")
        atoms_dict = row["atoms"]
        struct = _atoms_to_structure(atoms_dict)
        structures[row["jid"]] = struct
        if struct is None:
            n_failed += 1
            formulas.append(None)
            formula_anons.append(None)
            spacegroups.append(None)
            elems_col.append(None)
            nelems_col.append(None)
            continue
        formulas.append(struct.composition.reduced_formula)
        formula_anons.append(struct.composition.anonymized_formula)
        sg = _spacegroup_number(struct)
        spacegroups.append(sg)
        elems = sorted(str(e) for e in struct.composition.elements)
        elems_col.append(elems)
        nelems_col.append(len(elems))

    if n_failed:
        print(f"WARNING: {n_failed} atoms -> Structure conversions failed (None prototype_key)")

    df["formula"] = formulas
    df["formula_anonymous"] = formula_anons
    df["spacegroup"] = spacegroups
    df["elements"] = elems_col
    df["nelements"] = nelems_col
    df["prototype_key"] = [
        f"{fa}_{sg}" if fa is not None and sg is not None else None
        for fa, sg in zip(formula_anons, spacegroups)
    ]

    n_dup_formula = df["formula"].duplicated().sum()
    print(f"Duplicate formulas (kept)  : {n_dup_formula}")

    # Rename: Tc -> ref_tc (computed reference labels; DFPT + Allen-Dynes; NOT experimental)
    df = df.rename(columns={"Tc": "ref_tc"})

    # ── Group IDs: union-find over prototype_key OR formula ───────────────────
    df["group_id"] = build_group_ids(df)
    n_groups = df["group_id"].nunique()
    print(f"Group IDs (prototype+formula union-find): {n_groups}")

    return df, structures


# ── Split + leakage guard ─────────────────────────────────────────────────────

def split_by_group(
    df: pd.DataFrame,
    group_col: str,
    holdout_frac: float = 0.25,
    seed: int = 42,
) -> pd.DataFrame:
    """Add 'split' column (train/holdout).

    Row-based stopping: shuffle groups with seeded RNG, add whole groups to
    holdout until holdout rows >= holdout_frac * total rows.  Stops at the
    first group that reaches the target; never splits a group.  Deterministic.
    """
    rng = np.random.default_rng(seed)
    groups = np.array(sorted(df[group_col].dropna().unique()))
    rng.shuffle(groups)

    target = holdout_frac * len(df)
    group_sizes = df.groupby(group_col).size()

    holdout_set: set = set()
    holdout_rows = 0
    for g in groups:
        holdout_set.add(g)
        holdout_rows += int(group_sizes.get(g, 0))
        if holdout_rows >= target:
            break

    df = df.copy()
    df["split"] = df[group_col].apply(
        lambda g: "holdout" if g in holdout_set else "train"
    )
    return df


def assign_folds(df: pd.DataFrame, n_folds: int = 5) -> pd.DataFrame:
    """Assign fold indices (0..n_folds-1). Deterministic, no randomness.

    Sort groups by size descending (tie-break: group_id ascending).
    Assign each group to the fold with fewest rows so far (tie-break: lowest fold index).
    Adds column 'fold' (int).
    """
    group_sizes = (
        df.groupby("group_id")
        .size()
        .rename("size")
        .reset_index()
        .sort_values(["size", "group_id"], ascending=[False, True])
    )

    fold_row_counts = [0] * n_folds
    group_to_fold: dict[int, int] = {}

    for _, row in group_sizes.iterrows():
        gid = int(row["group_id"])
        sz = int(row["size"])
        best = min(range(n_folds), key=lambda f: (fold_row_counts[f], f))
        group_to_fold[gid] = best
        fold_row_counts[best] += sz

    df = df.copy()
    df["fold"] = df["group_id"].map(group_to_fold).astype(int)
    return df


def assert_fold_integrity(df: pd.DataFrame) -> None:
    """Raise AssertionError if fold assignment violates group isolation.

    Checks:
    1. Each group_id appears in exactly one fold.
    2. No prototype_key appears in more than one fold.
    3. No reduced formula appears in more than one fold (if column present).
    """
    errors: list[str] = []

    group_fold_counts = df.groupby("group_id")["fold"].nunique()
    bad_groups = group_fold_counts[group_fold_counts > 1]
    if len(bad_groups):
        errors.append(
            f"{len(bad_groups)} group_id(s) span >1 fold: {sorted(bad_groups.index[:3])}"
        )

    proto_fold_counts = (
        df[df["prototype_key"].notna()].groupby("prototype_key")["fold"].nunique()
    )
    bad_proto = proto_fold_counts[proto_fold_counts > 1]
    if len(bad_proto):
        errors.append(
            f"prototype_key(s) span >1 fold: {sorted(bad_proto.index[:3])}"
        )

    if "formula" in df.columns:
        formula_fold_counts = (
            df[df["formula"].notna()].groupby("formula")["fold"].nunique()
        )
        bad_formula = formula_fold_counts[formula_fold_counts > 1]
        if len(bad_formula):
            errors.append(
                f"formula(s) span >1 fold: {sorted(bad_formula.index[:3])}"
            )

    if errors:
        raise AssertionError("Fold integrity failed: " + "; ".join(errors))


def assert_no_leakage(df: pd.DataFrame) -> None:
    """Raise AssertionError if any prototype_key OR formula appears in both splits."""
    train = df[df["split"] == "train"]
    holdout = df[df["split"] == "holdout"]
    errors: list[str] = []

    overlap_proto = (
        set(train["prototype_key"].dropna()) & set(holdout["prototype_key"].dropna())
    )
    if overlap_proto:
        errors.append(
            f"prototype_key leakage ({len(overlap_proto)}): {sorted(overlap_proto)[:3]}"
        )

    if "formula" in df.columns:
        overlap_formula = (
            set(train["formula"].dropna()) & set(holdout["formula"].dropna())
        )
        if overlap_formula:
            errors.append(
                f"formula leakage ({len(overlap_formula)}): {sorted(overlap_formula)[:3]}"
            )

    if errors:
        raise AssertionError("Leakage: " + "; ".join(errors))
