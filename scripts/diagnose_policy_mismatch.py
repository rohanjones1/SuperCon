"""Diagnose classification differences between the policy builder and the deployed screen().

    uv run python scripts/diagnose_policy_mismatch.py

A  = scripts/build_stability_policy.py logic as originally run: pandas.read_csv (default float parser),
     error.isna() rows, chgnet_hull_<frozen> <= cutoff.
A2 = same, but pandas float_precision="round_trip".
B  = deployed path: lab.tools_api._load_cache (csv module + float()), lab.decision.screen() via screen_batch.

The cutoff is read from reports/stability_policy.json. Read-only: writes nothing.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pandas as pd

from lab import run_batch as rb
from lab import tools_api as ta
from lab.decision import load_policy

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "reports" / "stability_calibration.csv"
POLICY_PATH = ROOT / "reports" / "stability_policy.json"
GT = 0.05


def main() -> None:
    policy = load_policy(POLICY_PATH)
    cutoff = policy["screening_cutoff_ev_per_atom"]
    col = f"chgnet_hull_{policy['frozen_convention']}"
    print(f"policy cutoff repr: {cutoff!r}   column: {col}   policy counts: "
          f"TP {policy['tp']} FP {policy['fp']} FN {policy['fn']} TN {policy['tn']}")

    raw = {r["material_id"]: r for r in csv.DictReader(open(CSV_PATH, newline="", encoding="utf-8"))}

    def builder(float_precision):
        df = pd.read_csv(CSV_PATH, float_precision=float_precision)
        df = df[df["error"].isna()]
        return {r.material_id: float(getattr(r, col)) for r in df.itertuples()}

    a, a2 = builder(None), builder("round_trip")
    cache = ta._load_cache(CSV_PATH, col)
    cands = [rb.CandidateStabilityResult(m, cache[m]["formula"], float(cache[m][col])) for m in sorted(cache)]
    b = {c.candidate_id: c for c in rb.screen_batch(cands, policy)}

    print(f"rows: A {len(a)}  A2 {len(a2)}  B {len(b)}")
    print(f"retained: A {sum(v <= cutoff for v in a.values())}  A2 {sum(v <= cutoff for v in a2.values())}  "
          f"B {sum(c.screening_decision == 'RETAIN' for c in b.values())}")

    mismatches = 0
    for m in sorted(set(a) | set(b)):
        a_dec = ("RETAIN" if a[m] <= cutoff else "DEPRIORITIZE") if m in a and a[m] == a[m] else "missing"
        b_dec = b[m].screening_decision if m in b else "missing"
        if a_dec != b_dec:
            mismatches += 1
            r = raw.get(m, {})
            print(f"\nMISMATCH {m} {r.get('formula')}")
            print(f"  csv raw string      : {r.get(col)!r}")
            print(f"  A pandas default    : {a.get(m)!r}")
            print(f"  A2 pandas round_trip: {a2.get(m)!r}")
            print(f"  B float() (deployed): {b[m].chgnet_hull_ev_per_atom!r}" if m in b else "  B: not in cache")
            print(f"  cutoff              : {cutoff!r}")
            print(f"  policy-side (A)     : {a_dec}    M6-side (B): {b_dec}")
            print(f"  error field         : {r.get('error')!r}")
    print(f"\nclassification mismatches A vs B: {mismatches}")

    # Ground-truth column comparison (retrospective labels only)
    for gt_col in ("mp_energy_above_hull_gga", "mp_energy_above_hull_summary"):
        scr = [rb.CandidateStabilityResult(c.candidate_id, c.formula, c.chgnet_hull_ev_per_atom,
                                           mp_hull_ev_per_atom=ta._parse_float(cache[c.candidate_id][gt_col]),
                                           screening_decision=c.screening_decision) for c in b.values()]
        mt = rb.retrospective_metrics(scr, gt_threshold=GT)
        print(f"B with GT {gt_col}: TP {mt['tp']} FP {mt['fp']} FN {mt['fn']} TN {mt['tn']}")
    def stable(m, gt_col):
        v = ta._parse_float(cache[m][gt_col])
        return None if v is None else v <= GT

    diff = [m for m in sorted(cache)
            if stable(m, "mp_energy_above_hull_gga") != stable(m, "mp_energy_above_hull_summary")]
    for m in diff:
        print(f"GT label differs by column: {m} {cache[m]['formula']} gga={cache[m]['mp_energy_above_hull_gga']} "
              f"summary={cache[m]['mp_energy_above_hull_summary']}")
    print(json.dumps({"mismatches": mismatches, "gt_label_differences": diff}))


if __name__ == "__main__":
    main()
