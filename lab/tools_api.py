"""lab/tools_api.py — Batch/ID-based functions exposed to agents.

Public API
----------
lookup_screening(material_ids)  Cache-backed RETAIN / DEPRIORITIZE lookup (no compute).
get_policy()                    Read-only stability policy + caveat.

Constraints
-----------
* No omnigent imports (science core must run standalone).
* No CHGNet, no mp-api, no network. Nothing is computed here: CHGNet hulls are
  read from the calibration CSV and decisions come from lab.run_batch.screen_batch.
* Every candidate returned is IN-SAMPLE (calibration run). See D19.
* Functions never raise; failures are returned as {"error": ...}.
"""
from __future__ import annotations

import csv
import json
import math
import time
from pathlib import Path

from lab import run_batch as _rb
from lab.decision import load_policy

_REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CSV_PATH = _REPO_ROOT / "reports" / "stability_calibration.csv"
DEFAULT_POLICY_PATH = _REPO_ROOT / "reports" / "stability_policy.json"

MAX_IDS_PER_CALL = 5
TIER = "T1"
GROUND_TRUTH_CRITERION_EV_PER_ATOM = 0.05

CAVEAT = (
    "CHGNet triage only, not DFT validation. DEPRIORITIZE does not mean unstable. "
    "Cutoff was calibrated in-sample on 39 compounds; precision/recall unverified on held-out data."
)
NOT_IN_CACHE_NOTE = "No precomputed CHGNet hull. This tool does not compute."
MP_DFT_HULL_LABEL = "retrospective ground truth, unavailable for novel candidates"
# Mirrors lab/run_batch.py: validation_priority = chgnet_hull_ev_per_atom ("lower = higher
# urgency"); build_validation_queue sorts ascending, ties by candidate_id, index 0 = most urgent.
PRIORITY_NOTE = (
    "validation_priority equals the CHGNet hull (eV/atom). LOWER value = validate first "
    "(build_validation_queue ranks RETAIN candidates ascending, ties broken by material_id). "
    "It is a queue order for future DFT/experiment, not a stability claim."
)
TIMING_NOTE = "wall_ms is a cache lookup, not CHGNet compute time."

# MP hull column used as retrospective ground truth (assumption: MP summary hull,
# i.e. MP2020-corrected GGA/GGA+U mixing; see STATUS.md open issue).
MP_DFT_HULL_COLUMN = "mp_energy_above_hull_summary"


def _parse_float(value) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _load_cache(csv_path: Path, hull_column: str) -> dict[str, dict]:
    """Return {material_id: row} for rows with a usable CHGNet hull and no error."""
    cache: dict[str, dict] = {}
    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if hull_column not in (reader.fieldnames or []):
            raise KeyError(f"CSV has no column {hull_column!r}")
        for row in reader:
            if (row.get("error") or "").strip():
                continue
            if _parse_float(row.get(hull_column)) is None:
                continue
            cache[row["material_id"]] = row
    return cache


def _error(message: str, t0: float) -> dict:
    return {
        "error": message,
        "caveat": CAVEAT,
        "priority_note": PRIORITY_NOTE,
        "timing_note": TIMING_NOTE,
        "wall_ms": (time.perf_counter() - t0) * 1000.0,
    }


def lookup_screening(
    material_ids: list[str],
    csv_path: str | Path | None = None,
    policy_path: str | Path | None = None,
    include_ground_truth: bool = False,
) -> dict:
    """Look up cached CHGNet hulls and return screen_batch decisions for <= 5 ids.

    MP DFT hull (retrospective ground truth) is included only if include_ground_truth=True.
    """
    t0 = time.perf_counter()
    try:
        if not isinstance(material_ids, list) or not all(isinstance(m, str) for m in material_ids):
            return _error("material_ids must be a list of strings", t0)
        if len(material_ids) > MAX_IDS_PER_CALL:
            return _error(f"too many ids: {len(material_ids)} > {MAX_IDS_PER_CALL} per call", t0)

        policy = load_policy(policy_path or DEFAULT_POLICY_PATH)
        frozen = policy["frozen_convention"]
        hull_column = f"chgnet_hull_{frozen}"
        cache = _load_cache(Path(csv_path or DEFAULT_CSV_PATH), hull_column)

        ids = list(dict.fromkeys(material_ids))  # dedupe, keep order
        found = [m for m in ids if m in cache]
        candidates = [
            _rb.CandidateStabilityResult(
                candidate_id=m,
                formula=cache[m]["formula"],
                chgnet_hull_ev_per_atom=_parse_float(cache[m][hull_column]),
            )
            for m in found
        ]
        screened = {c.candidate_id: c for c in _rb.screen_batch(candidates, policy)}

        source = f"calibration_run_{policy['calibration_run_id']}"
        results = []
        for m in ids:
            if m not in screened:
                results.append({"material_id": m, "status": "not_in_cache", "note": NOT_IN_CACHE_NOTE})
                continue
            c = screened[m]
            item = {
                "material_id": c.candidate_id,
                "formula": c.formula,
                "screening_decision": c.screening_decision,
                "validation_priority": c.validation_priority,
                "decision_reason": c.decision_reason,
                "screening_cutoff_ev_per_atom": c.screening_cutoff_ev_per_atom,
                "policy_calibration_run_id": c.policy_calibration_run_id,
                "chgnet_hull_ev_per_atom": c.chgnet_hull_ev_per_atom,
                "in_sample": True,
                "source": source,
            }
            if include_ground_truth and MP_DFT_HULL_COLUMN in cache[m]:
                item["mp_dft_hull_ev_per_atom"] = _parse_float(cache[m][MP_DFT_HULL_COLUMN])
                item["mp_dft_hull_label"] = MP_DFT_HULL_LABEL
            results.append(item)

        return {
            "tier": TIER,
            "frozen_convention": frozen,
            "ground_truth_criterion_ev_per_atom": GROUND_TRUTH_CRITERION_EV_PER_ATOM,
            "caveat": CAVEAT,
            "priority_note": PRIORITY_NOTE,
            "timing_note": TIMING_NOTE,
            "results": results,
            "wall_ms": (time.perf_counter() - t0) * 1000.0,
        }
    except Exception as exc:  # never raise to the agent
        return _error(f"{type(exc).__name__}: {exc}", t0)


def get_policy(policy_path: str | Path | None = None) -> dict:
    """Return the stability policy JSON plus the caveat. Read-only."""
    try:
        policy = json.loads(Path(policy_path or DEFAULT_POLICY_PATH).read_text(encoding="utf-8"))
        return {**policy, "caveat": CAVEAT}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "caveat": CAVEAT}
