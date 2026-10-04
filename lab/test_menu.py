"""lab/test_menu.py — Planner-facing menu of available tests and the human-set budget.

Public API
----------
get_test_menu() -> dict   Three tests with measured performance/cost, read from report files.
get_budget()    -> dict   Human-set budget (experiments/budget_001.yaml) + derived CHGNet coverage.

Constraints
-----------
* Stdlib only (no chgnet / pymatgen / yaml imports).
* No hardcoded performance numbers: every value is read from reports/ at call time
  and carries a `source` field naming the file and key.
* Functions never raise; failures are returned as {"error": ...}.
"""
from __future__ import annotations

import csv
import json
import math
import statistics
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORTS_DIR = _REPO_ROOT / "reports"
DEFAULT_BUDGET_PATH = _REPO_ROOT / "experiments" / "budget_001.yaml"

ARM_A_FILE = "arm_a_results_run1.json"
CALIBRATION_FILE = "stability_calibration.json"
POLICY_FILE = "stability_policy.json"
CALIBRATION_CSV = "stability_calibration.csv"

# Scope of the CSV wall_ms timer, from lab/relax.py::relax_structure (t0 is taken before the
# lazy CHGNet import + StructOptimizer() and stopped after optimizer.relax) and
# scripts/calibrate_stability.py (row wall_ms = res["wall_ms"]; e_above_hull runs afterwards).
RELAX_TIMER_SCOPE = "relaxation only"
RELAX_TIMER_NOTE = (
    "relaxation only (includes per-call CHGNet import and model construction); "
    "excludes hull step and cached MP competing-phase fetch"
)
COST_BASIS_NOTE = (
    "Upper bound. Based on relaxation-only median (includes model load); excludes the hull step "
    "and Materials Project competing-phase fetch, so end-to-end cost per novel candidate is "
    "unmeasured and likely higher."
)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _get(d: dict, dotted: str):
    """Fetch d[k1][k2]... for a dotted key path (keys may themselves contain '.')."""
    cur = d
    for key in _split_keys(cur, dotted):
        cur = cur[key]
    return cur


def _split_keys(d: dict, dotted: str) -> list[str]:
    # Greedy match so keys like "c_stability_classification_at_0.05" resolve.
    parts = dotted.split(".")
    keys, cur, i = [], d, 0
    while i < len(parts):
        for j in range(len(parts), i, -1):
            cand = ".".join(parts[i:j])
            if isinstance(cur, dict) and cand in cur:
                keys.append(cand)
                cur = cur[cand]
                i = j
                break
        else:
            raise KeyError(dotted)
    return keys


def _num(data: dict, fname: str, key: str, ci_key: str | None = None, ci_name: str = "ci_95") -> dict:
    out = {"value": _get(data, key), "source": f"reports/{fname}:{key}"}
    if ci_key is not None:
        out[ci_name] = list(_get(data, ci_key))
        out["source"] += f" ; {ci_key}"
    return out


def _median_relax_s(csv_path: Path) -> tuple[float, int]:
    """Median of wall_ms/1000 over rows with an empty error field. Returns (median_s, n_rows)."""
    vals: list[float] = []
    with open(csv_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if (row.get("error") or "").strip():
                continue
            try:
                ms = float(row["wall_ms"])
            except (KeyError, TypeError, ValueError):
                continue
            if math.isfinite(ms):
                vals.append(ms / 1000.0)
    if not vals:
        raise ValueError(f"no usable wall_ms rows in {csv_path.name}")
    return statistics.median(vals), len(vals)


def get_test_menu(reports_dir: str | Path | None = None) -> dict:
    """Return the three-test menu with measured numbers read from report files."""
    try:
        rdir = Path(reports_dir or DEFAULT_REPORTS_DIR)
        arm_a = _read_json(rdir / ARM_A_FILE)
        cal = _read_json(rdir / CALIBRATION_FILE)
        pol = _read_json(rdir / POLICY_FILE)
        median_s, n_rows = _median_relax_s(rdir / CALIBRATION_CSV)

        auroc = _num(arm_a, ARM_A_FILE, "point_estimates.proxy.auroc", "bootstrap_ci.proxy.auroc_ci")
        enrichment = _num(arm_a, ARM_A_FILE, "point_estimates.proxy.enrichment",
                          "bootstrap_ci.proxy.enrichment_ci")
        lo, hi = auroc["ci_95"]
        proxy_limits = [
            "trained on computed reference labels (JARVIS DFPT + Allen-Dynes), not experimental Tc",
            "evaluated family-held-out (grouped CV over group_id)",
        ]
        if lo <= 0.5 <= hi:
            proxy_limits.append("AUROC CI includes 0.5 (not distinguishable from random)")

        c05 = "metrics.c_stability_classification_at_0.05"
        return {
            "tests": {
                "composition_proxy": {
                    "tier": "T1",
                    "runnable_in_this_lab": True,
                    "measured_performance": {"auroc": auroc, "enrichment": enrichment},
                    "measured_cost_s_per_candidate": None,
                    "cost_note": "not separately measured",
                    "limits": proxy_limits,
                },
                "chgnet_triage": {
                    "tier": "T1",
                    "runnable_in_this_lab": True,
                    "how_to_read": (
                        "deployed_policy_in_sample is the policy lookup_screening applies; "
                        "pre_registered_at_0.05 is the un-tuned result."
                    ),
                    "pre_registered_at_0.05": {
                        "precision": _num(cal, CALIBRATION_FILE, f"{c05}.precision",
                                          f"{c05}.precision_wilson_ci_95", "wilson_ci_95"),
                        "recall": _num(cal, CALIBRATION_FILE, f"{c05}.recall",
                                       f"{c05}.recall_wilson_ci_95", "wilson_ci_95"),
                        "hull_mae_ev_per_atom": _num(cal, CALIBRATION_FILE, "metrics.b_hull_mae.corrected_ref"),
                        "verdict": _num(cal, CALIBRATION_FILE, "verdict.message"),
                    },
                    "deployed_policy_in_sample": {
                        "screening_cutoff_ev_per_atom": _num(pol, POLICY_FILE, "screening_cutoff_ev_per_atom"),
                        "precision": _num(pol, POLICY_FILE, "precision_at_cutoff",
                                          "precision_wilson_ci_95", "wilson_ci_95"),
                        "recall": _num(pol, POLICY_FILE, "recall_at_cutoff",
                                       "recall_wilson_ci_95", "wilson_ci_95"),
                        "note": (
                            "cutoff chosen on the same 39 compounds; in-sample design fit, "
                            "likely optimistic; not held-out"
                        ),
                    },
                    "measured_cost_s_per_candidate": {
                        "value": median_s,
                        "statistic": "median",
                        "n_rows_used": n_rows,
                        "scope": RELAX_TIMER_SCOPE,
                        "note": RELAX_TIMER_NOTE,
                        "source": f"reports/{CALIBRATION_CSV}:wall_ms (rows with empty error)",
                    },
                    "limits": [
                        "cutoff calibrated in-sample on 39 compounds",
                        "misses some stable compounds (recall < 1)",
                    ],
                },
                "dft_validation": {
                    "tier": "T3",
                    "runnable_in_this_lab": False,
                    "measured_performance": None,
                    "measured_cost_s_per_candidate": None,
                    "note": "not available; script generation only",
                },
            },
        }
    except Exception as exc:  # never raise to the agent
        return {"error": f"{type(exc).__name__}: {exc}"}


def _parse_flat_yaml(text: str) -> dict:
    """Parse flat `key: value` YAML (ints, floats, quoted/unquoted strings). No nesting."""
    out: dict = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        key, sep, val = s.partition(":")
        if not sep:
            raise ValueError(f"unparseable line: {line!r}")
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            out[key.strip()] = val[1:-1]
            continue
        for cast in (int, float):
            try:
                out[key.strip()] = cast(val)
                break
            except ValueError:
                pass
        else:
            out[key.strip()] = val
    return out


def get_budget(
    budget_path: str | Path | None = None,
    reports_dir: str | Path | None = None,
) -> dict:
    """Return the human-set budget plus CHGNet coverage derived from measured median cost."""
    try:
        bpath = Path(budget_path or DEFAULT_BUDGET_PATH)
        budget = _parse_flat_yaml(bpath.read_text(encoding="utf-8"))
        total_s = budget["total_compute_budget_s"]
        pool = budget["candidate_pool_size"]
        median_s, n_rows = _median_relax_s(Path(reports_dir or DEFAULT_REPORTS_DIR) / CALIBRATION_CSV)
        affordable = math.floor(total_s / median_s)
        return {
            **budget,
            "source": str(bpath.relative_to(_REPO_ROOT)) if bpath.is_relative_to(_REPO_ROOT) else str(bpath),
            "spent_s": 0,
            "spend_tracking": "not wired yet",
            "chgnet_median_s_per_candidate": {
                "value": median_s,
                "n_rows_used": n_rows,
                "note": RELAX_TIMER_NOTE,
                "source": f"reports/{CALIBRATION_CSV}:wall_ms (rows with empty error)",
            },
            "affordable_chgnet_candidates_upper_bound": affordable,
            "coverage_fraction_upper_bound": min(1.0, affordable / pool),
            "cost_basis_note": COST_BASIS_NOTE,
        }
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
