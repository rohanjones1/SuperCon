"""scripts/run_arm_a.py — Run the Arm A pre-registered benchmark.

Loads data/processed/seeds.csv, verifies prereg SHA256, runs 5-fold CV,
records to ledger, writes reports/.

Usage:
    uv run python scripts/run_arm_a.py [--seeds PATH] [--prereg PATH] [--ledger PATH]
"""
from __future__ import annotations

import argparse
import json
import platform
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import sklearn

from lab.benchmark import run_arm_a, verify_prereg
from lab.features import build_feature_matrix
from lab.ledger import (
    DEFAULT_PATH as LEDGER_DEFAULT,
    init_db,
    record_result,
    record_run,
    record_timing,
)

SEEDS_DEFAULT = Path("data/processed/seeds.csv")
PREREG_DEFAULT = Path("experiments/prereg/prereg_001.yaml")
REPORTS_DIR = Path("reports")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_prior_runs(ledger_path: Path) -> int:
    """Return number of prior arm_a_benchmark runs in ledger."""
    con = sqlite3.connect(ledger_path)
    rows = con.execute(
        "SELECT id FROM runs WHERE tool = 'arm_a_benchmark' ORDER BY id"
    ).fetchall()
    con.close()
    return len(rows)


def _print_summary(res: dict, run_id: int) -> None:
    """Print compact results table."""
    pt = res["point_estimates"]
    ci = res["bootstrap_ci"]
    v = res["verdicts"]

    print(f"\n{'='*60}")
    print(f"  Arm A benchmark  —  run_id={run_id}")
    print(f"  n={res['n_samples']}  positives={res['n_positive']}  base_rate={res['base_rate']:.3f}")
    print(f"{'='*60}")
    print(f"  {'Scorer':<10}  {'AUROC':>6}  {'CI 95%':>18}  {'Hit@10%':>8}  {'Enrich':>7}")
    print(f"  {'-'*10}  {'-'*6}  {'-'*18}  {'-'*8}  {'-'*7}")
    for key in ("proxy", "B1", "B0"):
        auroc = pt[key]["auroc"]
        lo, hi = ci[key]["auroc_ci"]
        hit = pt[key]["hit_rate_top10"]
        enr = pt[key]["enrichment"]
        print(f"  {key:<10}  {auroc:6.3f}  [{lo:.3f}, {hi:.3f}]  {hit:8.3f}  {enr:7.2f}")
    print()

    h1 = v["H1"]
    h2 = v["H2"]
    print(f"  H1  AUROC={h1['auroc']:.3f}  CI=[{h1['auroc_ci'][0]:.3f},{h1['auroc_ci'][1]:.3f}]"
          f"  → {h1['verdict'].upper()}")
    print(f"  H2  proxy={h2['proxy_auroc']:.3f}  B1={h2['B1_auroc']:.3f}"
          f"  diff_CI=[{h2['diff_ci'][0]:.3f},{h2['diff_ci'][1]:.3f}]"
          f"  → {h2['verdict'].upper()}")

    loso = res["sensitivity"]["loso"]
    if loso:
        print(f"\n  Leave-one-group-out sensitivity:")
        for gid, g in loso.items():
            lo, hi = g["proxy_auroc_ci"]
            print(f"    drop group {gid}: proxy={g['proxy_auroc']:.3f}"
                  f"  B1={g['B1_auroc']:.3f}  CI=[{lo:.3f},{hi:.3f}]")

    wg = res["sensitivity"]["within_group_auroc"]
    print(f"\n  Within-group AUROC (qualifying groups n={wg['n_qualifying_groups']}):"
          f"  median={wg['median_auroc']:.3f}  "
          f"range=[{wg['min_auroc']:.3f},{wg['max_auroc']:.3f}]")

    exp10 = res["sensitivity"]["exploratory_10k"]
    if exp10:
        lo, hi = exp10["proxy_auroc_ci"]
        print(f"\n  Exploratory (threshold 10K, n_pos={exp10['n_positive']}):"
              f"  proxy={exp10['proxy_auroc']:.3f}"
              f"  B1={exp10['B1_auroc']:.3f}"
              f"  CI=[{lo:.3f},{hi:.3f}]")

    print(f"{'='*60}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Arm A pre-registered benchmark.")
    parser.add_argument("--seeds", type=Path, default=SEEDS_DEFAULT)
    parser.add_argument("--prereg", type=Path, default=PREREG_DEFAULT)
    parser.add_argument("--ledger", type=Path, default=LEDGER_DEFAULT)
    args = parser.parse_args()

    seeds_path: Path = args.seeds
    prereg_path: Path = args.prereg
    ledger_path: Path = args.ledger

    # ---- Init ledger ----
    init_db(ledger_path)

    # ---- RERUN detection ----
    n_prior = _check_prior_runs(ledger_path)
    if n_prior > 0:
        print(f"RERUN #{n_prior + 1} (prior arm_a_benchmark runs in ledger: {n_prior})")
    else:
        print("First arm_a_benchmark run.")

    # ---- Verify prereg integrity ----
    print(f"Verifying prereg: {prereg_path}")
    verify_prereg(seeds_path, prereg_path)
    print("  SHA256 OK.")

    # ---- Load data ----
    print(f"Loading {seeds_path} ...")
    df = pd.read_csv(seeds_path)
    print(f"  {len(df)} rows, {df['label'].sum()} positives, {df['fold'].nunique()} folds.")

    # ---- Build feature matrix ----
    print("Building feature matrix ...")
    t_feat_start = _utc_iso()
    X = build_feature_matrix(df["formula"])
    t_feat_end = _utc_iso()

    # ---- Run benchmark ----
    print("Running Arm A benchmark (5-fold CV + bootstrap) ...")
    t_run_start = _utc_iso()
    wall_start = time.time()
    results, oof_df = run_arm_a(df, X)
    wall_ms = (time.time() - wall_start) * 1000
    t_run_end = _utc_iso()

    # ---- Record to ledger ----
    hardware = f"{platform.node()} {platform.processor()}"
    run_id = record_run(
        tool="arm_a_benchmark",
        tool_version=f"sklearn={sklearn.__version__}",
        model_checkpoint=None,
        seed=0,
        hardware=hardware,
        wall_ms=wall_ms,
        path=ledger_path,
    )
    print(f"Recorded run_id={run_id}  wall={wall_ms/1000:.1f}s")

    # Record aggregate metrics per scorer
    for key in ("proxy", "B1", "B0"):
        pt = results["point_estimates"][key]
        ci = results["bootstrap_ci"][key]
        record_result(run_id, None, 1, f"{key}_auroc", pt["auroc"], path=ledger_path)
        record_result(run_id, None, 1, f"{key}_auroc_ci_lo", ci["auroc_ci"][0], path=ledger_path)
        record_result(run_id, None, 1, f"{key}_auroc_ci_hi", ci["auroc_ci"][1], path=ledger_path)
        record_result(run_id, None, 1, f"{key}_hit_rate_top10", pt["hit_rate_top10"], path=ledger_path)
        record_result(run_id, None, 1, f"{key}_enrichment", pt["enrichment"], path=ledger_path)

    diff_ci = results["bootstrap_ci"]["proxy_minus_B1"]["auroc_diff_ci"]
    record_result(run_id, None, 1, "proxy_minus_B1_auroc_diff_ci_lo", diff_ci[0], path=ledger_path)
    record_result(run_id, None, 1, "proxy_minus_B1_auroc_diff_ci_hi", diff_ci[1], path=ledger_path)

    # Record timing
    record_timing("feature_build", t_feat_start, t_feat_end, path=ledger_path)
    record_timing("arm_a_cv", t_run_start, t_run_end, path=ledger_path)

    # ---- Write reports ----
    REPORTS_DIR.mkdir(exist_ok=True)
    results_path = REPORTS_DIR / f"arm_a_results_run{run_id}.json"
    oof_path = REPORTS_DIR / f"arm_a_oof_scores_run{run_id}.csv"

    with open(results_path, "w") as f:
        json.dump(results, f, indent=2)
    oof_df.to_csv(oof_path, index=False)

    print(f"Results: {results_path}")
    print(f"OOF scores: {oof_path}")

    # ---- Print summary ----
    _print_summary(results, run_id)


if __name__ == "__main__":
    main()
