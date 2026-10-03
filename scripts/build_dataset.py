"""scripts/build_dataset.py — M2: build seeds.csv and structures.json.

Usage:
    uv run python scripts/build_dataset.py [--tc-threshold 5.0]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lab.data import (
    assert_fold_integrity,
    assert_no_leakage,
    assign_folds,
    load_supercon,
    split_by_group,
)

PROCESSED = ROOT / "data" / "processed"
PROCESSED.mkdir(parents=True, exist_ok=True)

parser = argparse.ArgumentParser()
parser.add_argument(
    "--tc-threshold", type=float, default=5.0,
    help="ref_tc threshold in K for binary label (default: 5.0)",
)
args = parser.parse_args()
TC_THRESHOLD = args.tc_threshold

print("=" * 60)
print(f"tc_threshold = {TC_THRESHOLD} K")
print("*** PROVISIONAL LABEL: ref_tc >= threshold ***")
print("*** ref_tc = computed reference (DFPT + Allen-Dynes), NOT experimental ***")
print("=" * 60)

# ── Load + annotate ───────────────────────────────────────────────────────────
df, structures = load_supercon(raw_dir=ROOT / "data" / "raw")

# ── Label ────────────────────────────────────────────────────────────────────
df["label"] = (df["ref_tc"] >= TC_THRESHOLD).astype(int)

# ── Split by group_id (union-find: prototype_key OR formula) ─────────────────
df = split_by_group(df, group_col="group_id", holdout_frac=0.25, seed=42)
assert_no_leakage(df)
print("Leakage check : PASS")

# ── Tc distribution ───────────────────────────────────────────────────────────
print("\n── ref_tc distribution (K, computed reference labels) ──────────────")
qs = df["ref_tc"].quantile([0.0, 0.10, 0.25, 0.50, 0.75, 0.90, 1.0])
for q, v in qs.items():
    print(f"  q{int(100*q):02d} : {v:.3f}")
for cut in [1, 5, 10, 20, 30]:
    n = int((df["ref_tc"] >= cut).sum())
    print(f"  ref_tc >= {cut:2d} K : {n:4d}  ({100 * n / len(df):.1f}%)")

# ── Group summary (union-find: prototype_key OR formula) ─────────────────────
n_groups = int(df["group_id"].nunique())
total_rows = len(df)
holdout_rows = int((df["split"] == "holdout").sum())

print(f"\n── Group summary (group_id = prototype_key ∪ formula union-find) ───")
print(f"  Total groups  : {n_groups}")
print(f"  Holdout rows  : {holdout_rows}  ({100 * holdout_rows / total_rows:.1f}%)")

group_sizes = df.groupby("group_id").size().sort_values(ascending=False)
group_pos = df[df["label"] == 1].groupby("group_id").size()
print(f"\n  8 largest groups:")
for gid, sz in group_sizes.head(8).items():
    pos = int(group_pos.get(gid, 0))
    print(f"    group_id {gid:4d}: {sz:4d} rows, {pos:3d} pos")

print(f"\n  Per-split breakdown:")
for split in ("train", "holdout"):
    sub = df[df["split"] == split]
    pos = int(sub["label"].sum())
    neg = len(sub) - pos
    base_rate = pos / len(sub) if len(sub) > 0 else 0.0
    print(
        f"    {split:8s}: {len(sub):4d} rows | {pos:3d} pos | {neg:3d} neg"
        f" | base_rate={base_rate:.3f}"
    )
    if split == "holdout" and pos < 10:
        print(f"\n  *** WARNING: holdout has only {pos} positive(s) (< 10). ***")
        print(f"  *** Evaluation will be unreliable. Record in DECISIONS.md. ***")
        print(f"  *** NOT auto-fixing. Lower --tc-threshold or revisit split. ***\n")

# Formula cross-contamination check (must be 0 after union-find split)
train_formulas = set(df.loc[df["split"] == "train", "formula"].dropna())
holdout_formulas = set(df.loc[df["split"] == "holdout", "formula"].dropna())
n_cross = len(train_formulas & holdout_formulas)
status = "PASS" if n_cross == 0 else "*** FAIL ***"
print(f"\n  Formulas in both splits: {n_cross}  ({status})")

# ── Fold assignment (5-fold, deterministic, no randomness) ───────────────────
df = assign_folds(df, n_folds=5)
assert_fold_integrity(df)
print("\n── Fold summary (5-fold, grouped, deterministic) ────────────────────")
for fold_idx in sorted(df["fold"].unique()):
    sub = df[df["fold"] == fold_idx]
    pos = int(sub["label"].sum())
    base_rate = pos / len(sub) if len(sub) > 0 else 0.0
    fold_group_sizes = sub.groupby("group_id").size()
    largest_gid = int(fold_group_sizes.idxmax())
    largest_sz = int(fold_group_sizes.max())
    print(
        f"  fold {fold_idx}: {len(sub):4d} rows | {pos:3d} pos"
        f" | base_rate={base_rate:.3f}"
        f" | largest group_id={largest_gid} ({largest_sz} rows)"
    )
print("Fold integrity check: PASS")

# ── Write seeds.csv ───────────────────────────────────────────────────────────
OUTPUT_COLS = [
    "jid", "formula", "formula_anonymous", "spacegroup", "prototype_key", "group_id",
    "elements", "nelements", "ref_tc", "lamb", "wlog", "press", "stability",
    "split", "fold", "label",
]
csv_df = df[OUTPUT_COLS].copy()
csv_df["elements"] = csv_df["elements"].apply(
    lambda x: "|".join(x) if isinstance(x, list) else ""
)

seeds_path = PROCESSED / "seeds.csv"
csv_df.to_csv(seeds_path, index=False)
sha = hashlib.sha256(seeds_path.read_bytes()).hexdigest()
print(f"\nseeds.csv SHA256 : {sha}")
print(f"Written          : {seeds_path}  ({len(csv_df)} rows)")

# ── Write structures.json ─────────────────────────────────────────────────────
jid_set = set(df["jid"])
struct_dict = {
    jid: (s.as_dict() if s is not None else None)
    for jid, s in structures.items()
    if jid in jid_set
}
struct_path = PROCESSED / "structures.json"
struct_path.write_text(json.dumps(struct_dict, default=str))
print(f"Written          : {struct_path}  ({len(struct_dict)} structures)")
