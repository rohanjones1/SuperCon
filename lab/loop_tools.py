"""lab/loop_tools.py — Deterministic tools for one discovery iteration (M6).

Hypothesis -> Planner -> Runner -> Analyst, handing off integer IDs only:

    get_candidate_pool()                      Hypothesis  pool IDs + formulas (no ground truth)
    register_hypothesis(...)  -> hypothesis_id Hypothesis  prereg row, agent_generated
    register_experiment_spec(...) -> spec_id   Planner     prereg row, validated against the test menu
    run_stability_screen(spec_id) -> run_id    Runner      screen_batch + validation queue + controls (blind)
    evaluate_iteration(run_id)                 Analyst     retrospective MP comparison AFTER screening
    record_next_decision(run_id, ...)          Analyst     agent_generated next decision + demo summary

Constraints
-----------
* No omnigent imports. No CHGNet, no mp-api, no network, no DFT: CHGNet hulls are read
  from the calibration-run cache via lab.tools_api; decisions come from lab.run_batch.screen_batch.
* The Runner path never reads or returns MP ground-truth columns.
* Every number is computed here or read from report files; LLM text is stored as agent_generated.
* Functions never raise; failures are returned as {"error": ...}.
"""
from __future__ import annotations

import json
import math
import platform
import time
from pathlib import Path

from lab import ledger as _ledger
from lab import run_batch as _rb
from lab import test_menu as _tm
from lab import tools_api as _ta
from lab.claims import scan_claims
from lab.decision import load_policy

_REPO_ROOT = Path(__file__).resolve().parents[1]

# Module-level paths (monkeypatched in tests).
LEDGER_PATH = _REPO_ROOT / "data" / "ledger.sqlite"
RUNS_DIR = _REPO_ROOT / "experiments" / "runs"
REPORTS_DIR = _REPO_ROOT / "reports"
CSV_PATH = _ta.DEFAULT_CSV_PATH
POLICY_PATH = _ta.DEFAULT_POLICY_PATH
BUDGET_PATH = _tm.DEFAULT_BUDGET_PATH

TOOL_VERSION = "m6-loop-1"
POOL_ID = "calibration_run_3"
RUNNABLE_TESTS = {"chgnet_triage"}  # only test wired for execution in this loop

# Pre-registered at hypothesis time (frozen into the hypothesis prereg content).
SUBSTANTIAL_REDUCTION_MAX_RETAINED_FRACTION = 0.5
POOL_REDUCTION_CHOICES = ("substantial", "minor")
RECALL_CHOICES = ("high", "not_high")

NEXT_DECISIONS = (
    "invalidate_run_no_strategy_update",
    "replan_before_scaling",
    "narrow_downstream_to_survivors",
    "shift_to_ranking_within_retained",
)
IN_SAMPLE_WARNING = (
    "Retrospective, in-sample: the screening cutoff was calibrated on this same pool against the MP "
    "labels, so recall/precision here are a calibration consistency check, not prospective performance "
    "and not DFT validation; generalization unverified."
)
REQUIRED_NEXT_VALIDATION = (
    "Held-out evaluation of the calibrated cutoff on compounds not used for calibration "
    "(requires a CHGNet compute tool; not available in this loop)."
)


def _err(exc_or_msg) -> dict:
    msg = exc_or_msg if isinstance(exc_or_msg, str) else f"{type(exc_or_msg).__name__}: {exc_or_msg}"
    return {"error": msg, "caveat": _ta.CAVEAT}


def _pool_rows() -> dict[str, dict]:
    policy = load_policy(POLICY_PATH)
    return _ta._load_cache(Path(CSV_PATH), f"chgnet_hull_{policy['frozen_convention']}")


def _prereg(kind: str, prereg_id: int) -> dict:
    row = _ledger.get_prereg(int(prereg_id), path=LEDGER_PATH)
    if row["content"].get("kind") != kind:
        raise ValueError(f"prereg {prereg_id} is not a {kind}")
    return row


def _write_once(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "x", encoding="utf-8") as fh:  # never overwrite a run artifact
        json.dump(data, fh, indent=2)


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Hypothesis
# ---------------------------------------------------------------------------

def get_candidate_pool() -> dict:
    """Return the cached candidate pool (IDs + formulas only; no ground truth)."""
    try:
        rows = _pool_rows()
        ids = sorted(rows)
        return {
            "pool_id": POOL_ID,
            "n_candidates": len(ids),
            "candidates": [{"material_id": m, "formula": rows[m]["formula"]} for m in ids],
            "selection_rule": "all cached compounds with a CHGNet hull, sorted by material_id",
            "note": "Known Materials Project compounds from calibration run 3 (in-sample). No ground truth exposed.",
        }
    except Exception as exc:
        return _err(exc)


def register_hypothesis(
    statement: str,
    predicted_pool_reduction: str,
    predicted_recall_of_mp_stable: str,
) -> dict:
    """Pre-register an agent hypothesis with categorical predictions. Returns hypothesis_id."""
    try:
        if not isinstance(statement, str) or not statement.strip() or len(statement) > 800:
            return _err("statement must be a non-empty string of at most 800 characters")
        if predicted_pool_reduction not in POOL_REDUCTION_CHOICES:
            return _err(f"predicted_pool_reduction must be one of {POOL_REDUCTION_CHOICES}")
        if predicted_recall_of_mp_stable not in RECALL_CHOICES:
            return _err(f"predicted_recall_of_mp_stable must be one of {RECALL_CHOICES}")
        policy = load_policy(POLICY_PATH)
        content = {
            "kind": "hypothesis",
            "label": "agent_generated",
            "pool_id": POOL_ID,
            "statement": statement.strip(),
            "predicted_pool_reduction": predicted_pool_reduction,
            "predicted_recall_of_mp_stable": predicted_recall_of_mp_stable,
            "frozen_thresholds": {
                "substantial_reduction_max_retained_fraction": SUBSTANTIAL_REDUCTION_MAX_RETAINED_FRACTION,
                "high_recall_min": policy["recall_target"],
                "high_recall_min_source": "reports/stability_policy.json:recall_target",
            },
            "registered_unix_s": time.time(),
        }
        _ledger.init_db(LEDGER_PATH)
        h = _ledger.record_prereg(content, path=LEDGER_PATH)
        hid = _ledger.get_prereg_id(h, path=LEDGER_PATH)
        return {"hypothesis_id": hid, "hash": h, "label": "agent_generated", "pool_id": POOL_ID,
                "frozen_thresholds": content["frozen_thresholds"]}
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------

def register_experiment_spec(
    hypothesis_id: int,
    chosen_test: str,
    alternatives_considered: str,
    expected_learning: str,
    stop_rule: str,
    unknowns: str = "",
) -> dict:
    """Validate the Planner's chosen test against the menu and pre-register the spec. Returns spec_id."""
    try:
        hyp = _prereg("hypothesis", hypothesis_id)
        menu = _tm.get_test_menu(Path(CSV_PATH).parent)
        if "error" in menu:
            return _err(f"test menu unavailable: {menu['error']}")
        tests = menu["tests"]
        if chosen_test not in tests:
            return _err(f"unknown test {chosen_test!r}; menu has {sorted(tests)}")
        if not tests[chosen_test]["runnable_in_this_lab"]:
            return _err(f"{chosen_test} is not runnable in this lab; choose a runnable test")
        if chosen_test not in RUNNABLE_TESTS:
            return _err(f"{chosen_test} is runnable in the lab but not wired into this loop (M6 wires {sorted(RUNNABLE_TESTS)})")
        budget = _tm.get_budget(BUDGET_PATH, Path(CSV_PATH).parent)
        if "error" in budget:
            return _err(f"budget unavailable: {budget['error']}")
        content = {
            "kind": "experiment_spec",
            "label": "agent_generated",
            "hypothesis_id": int(hypothesis_id),
            "hypothesis_hash": hyp["hash"],
            "chosen_test": chosen_test,
            "pool_id": hyp["content"]["pool_id"],
            "budget_id": budget.get("budget_id"),
            "max_candidates": budget["affordable_chgnet_candidates_upper_bound"],
            "max_candidates_source": "lab.test_menu.get_budget:affordable_chgnet_candidates_upper_bound",
            "alternatives_considered": str(alternatives_considered)[:1500],
            "expected_learning": str(expected_learning)[:1500],
            "stop_rule": str(stop_rule)[:800],
            "unknowns": str(unknowns)[:1000],
            "registered_unix_s": time.time(),
        }
        h = _ledger.record_prereg(content, path=LEDGER_PATH)
        sid = _ledger.get_prereg_id(h, path=LEDGER_PATH)
        return {"spec_id": sid, "hash": h, "chosen_test": chosen_test, "pool_id": content["pool_id"],
                "max_candidates": content["max_candidates"], "budget_id": content["budget_id"]}
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# Runner (blind to MP ground truth)
# ---------------------------------------------------------------------------

def _controls(policy: dict, candidates: list) -> list[dict]:
    """Known-answer controls on the decision layer + determinism check."""
    cutoff = policy["screening_cutoff_ev_per_atom"]
    pos = _rb.CandidateStabilityResult("ctrl_pos", "control", 0.0)
    neg = _rb.CandidateStabilityResult("ctrl_neg", "control", cutoff + 1.0)
    p, n = _rb.screen_batch([pos, neg], policy)
    first = [c.screening_decision for c in _rb.screen_batch(candidates, policy)]
    second = [c.screening_decision for c in _rb.screen_batch(candidates, policy)]
    return [
        {"control_id": "positive_hull_0", "expected": "RETAIN", "observed": p.screening_decision},
        {"control_id": "negative_hull_cutoff_plus_1", "expected": "DEPRIORITIZE", "observed": n.screening_decision},
        {"control_id": "determinism_rescreen", "expected": "identical", "observed": "identical" if first == second else "different"},
        {"control_id": "frozen_convention", "expected": "corrected", "observed": policy["frozen_convention"]},
    ]


def run_stability_screen(spec_id: int) -> dict:
    """Execute the registered spec on the cached pool. Returns run_id and blind counts."""
    t0 = time.perf_counter()
    try:
        spec = _prereg("experiment_spec", spec_id)["content"]
        if spec["chosen_test"] not in RUNNABLE_TESTS:
            return _err(f"spec chooses {spec['chosen_test']!r}, which this Runner cannot execute")
        policy = load_policy(POLICY_PATH)
        rows = _pool_rows()
        pool_ids = sorted(rows)
        selected = pool_ids[: int(spec["max_candidates"])]
        hull_col = f"chgnet_hull_{policy['frozen_convention']}"
        candidates = [
            _rb.CandidateStabilityResult(m, rows[m]["formula"], float(rows[m][hull_col])) for m in selected
        ]
        controls = _controls(policy, candidates)
        controls_passed = all(c["expected"] == c["observed"] for c in controls)

        _ledger.init_db(LEDGER_PATH)
        run_id = _ledger.record_run(
            tool="lab.loop_tools.run_stability_screen", tool_version=TOOL_VERSION,
            model_checkpoint=f"CHGNet hulls cached from {POOL_ID}", seed=None,
            hardware=platform.platform(), wall_ms=None, path=LEDGER_PATH, experiment_id=int(spec_id),
        )
        for c in candidates:
            c.ledger_candidate_id = _ledger.record_candidate(
                c.formula, hypothesis_id=str(spec["hypothesis_id"]), batch_id=f"run{run_id}",
                generator=f"{POOL_ID}_cache", path=LEDGER_PATH,
            )
        screened = _rb.screen_batch(candidates, policy)
        queue = _rb.build_validation_queue(screened)
        _rb.record_screening_to_ledger(run_id, screened, LEDGER_PATH)
        for c in controls:
            _ledger.record_control(run_id, c["control_id"], c["expected"], c["observed"],
                                   c["expected"] == c["observed"], path=LEDGER_PATH)

        n_ret = sum(1 for c in screened if c.screening_decision == "RETAIN")
        wall_ms = (time.perf_counter() - t0) * 1000.0
        manifest = {
            "run_id": run_id, "spec_id": int(spec_id), "hypothesis_id": spec["hypothesis_id"],
            "pool_id": POOL_ID, "chosen_test": spec["chosen_test"],
            "policy_calibration_run_id": policy["calibration_run_id"],
            "frozen_convention": policy["frozen_convention"],
            "screening_cutoff_ev_per_atom": policy["screening_cutoff_ev_per_atom"],
            "n_pool": len(pool_ids), "n_screened": len(screened),
            "n_retained": n_ret, "n_deprioritized": len(screened) - n_ret,
            "validation_queue": [c.candidate_id for c in queue],
            "candidates": [
                {"material_id": c.candidate_id, "formula": c.formula, "ledger_candidate_id": c.ledger_candidate_id,
                 "chgnet_hull_ev_per_atom": c.chgnet_hull_ev_per_atom,
                 "screening_decision": c.screening_decision, "validation_priority": c.validation_priority}
                for c in screened
            ],
            "controls": controls, "controls_passed": controls_passed,
            "wall_ms": wall_ms,
        }
        _write_once(Path(RUNS_DIR) / f"run_{run_id}.json", manifest)
        return {
            "run_id": run_id, "spec_id": int(spec_id), "status": "valid" if controls_passed else "invalid_controls_failed",
            "n_pool": manifest["n_pool"], "n_screened": manifest["n_screened"],
            "n_retained": n_ret, "n_deprioritized": manifest["n_deprioritized"],
            "validation_queue_size": len(queue), "validation_queue_top5": manifest["validation_queue"][:5],
            "controls": controls, "controls_passed": controls_passed,
            "screening_cutoff_ev_per_atom": manifest["screening_cutoff_ev_per_atom"],
            "policy_calibration_run_id": manifest["policy_calibration_run_id"],
            "priority_note": _ta.PRIORITY_NOTE, "caveat": _ta.CAVEAT,
            "wall_ms": wall_ms, "timing_note": _ta.TIMING_NOTE,
            "artifact": f"experiments/runs/run_{run_id}.json",
        }
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# Analyst (ground truth only after the screening decision exists)
# ---------------------------------------------------------------------------

def _decision_signal(controls_passed: bool, n_missing: int, retained_fraction: float, max_frac: float) -> str:
    if not controls_passed:
        return "invalidate_run_no_strategy_update"
    if n_missing > 0:
        return "replan_before_scaling"
    if retained_fraction <= max_frac:
        return "narrow_downstream_to_survivors"
    return "shift_to_ranking_within_retained"


def _finite(x):
    return x if isinstance(x, (int, float)) and math.isfinite(x) else None


def evaluate_iteration(run_id: int) -> dict:
    """Compare the registered prediction with the run, then with MP ground truth (retrospective)."""
    try:
        if (Path(RUNS_DIR) / f"run_{int(run_id)}_evaluation.json").exists():
            return _err(f"run {run_id} already evaluated; see experiments/runs/run_{int(run_id)}_evaluation.json")
        manifest = _read(Path(RUNS_DIR) / f"run_{int(run_id)}.json")
        run = _ledger.get_run(int(run_id), path=LEDGER_PATH)
        if run["experiment_id"] != manifest["spec_id"]:
            return _err("ledger run does not match run artifact")
        controls = _ledger.get_controls(int(run_id), path=LEDGER_PATH)
        if not controls:
            return _err("no controls recorded for this run; screening incomplete")
        controls_passed = all(bool(c["passed"]) for c in controls)
        spec = _prereg("experiment_spec", manifest["spec_id"])["content"]
        hyp = _prereg("hypothesis", spec["hypothesis_id"])["content"]
        thr = hyp["frozen_thresholds"]

        policy = load_policy(POLICY_PATH)
        gt_col = _ta.ground_truth_column(policy)
        rows = _pool_rows()
        if rows and any(gt_col not in r for r in rows.values()):
            return _err(f"ground-truth column {gt_col!r} (from policy) missing from {Path(CSV_PATH).name}")
        n_missing = sum(1 for c in manifest["candidates"] if c["material_id"] not in rows)
        screened = [
            _rb.CandidateStabilityResult(
                c["material_id"], c["formula"], c["chgnet_hull_ev_per_atom"],
                mp_hull_ev_per_atom=_ta._parse_float(rows.get(c["material_id"], {}).get(gt_col)),
                screening_decision=c["screening_decision"],
            )
            for c in manifest["candidates"]
        ]
        retro = _rb.retrospective_metrics(screened, gt_threshold=_ta.GROUND_TRUTH_CRITERION_EV_PER_ATOM)
        retained_fraction = retro["retention_fraction"]
        recall = retro["retrospective_recall"]

        observed_reduction = "substantial" if retained_fraction <= thr["substantial_reduction_max_retained_fraction"] else "minor"
        if not math.isfinite(recall):
            observed_recall = "indeterminate"
        else:
            observed_recall = "high" if recall >= thr["high_recall_min"] - 1e-12 else "not_high"
        matches = [observed_reduction == hyp["predicted_pool_reduction"],
                   observed_recall == hyp["predicted_recall_of_mp_stable"]]
        if not controls_passed:
            verdict = "invalid_controls_failed"
        elif observed_recall == "indeterminate":
            verdict = "inconclusive"
        else:
            verdict = {2: "supported", 1: "partially_supported", 0: "not_supported"}[sum(matches)]

        signal = _decision_signal(controls_passed, n_missing, retained_fraction,
                                  thr["substantial_reduction_max_retained_fraction"])

        _ledger.init_db(LEDGER_PATH)
        eval_run_id = _ledger.record_run(
            tool="lab.loop_tools.evaluate_iteration", tool_version=TOOL_VERSION, model_checkpoint=None,
            seed=None, hardware=platform.platform(), wall_ms=None, path=LEDGER_PATH,
            experiment_id=int(manifest["spec_id"]),
        )
        for metric in ("retention_fraction", "retrospective_recall", "retrospective_precision",
                       "tp", "fp", "fn", "tn"):
            v = _finite(retro[metric])
            if v is not None:
                _ledger.record_result(eval_run_id, None, 1, f"m6_{metric}", float(v), path=LEDGER_PATH)

        evaluation = {
            "run_id": int(run_id), "evaluation_run_id": eval_run_id,
            "spec_id": manifest["spec_id"], "hypothesis_id": spec["hypothesis_id"],
            "hypothesis_statement": hyp["statement"], "hypothesis_label": hyp["label"],
            "chosen_test": spec["chosen_test"],
            "prediction": {"pool_reduction": hyp["predicted_pool_reduction"],
                           "recall_of_mp_stable": hyp["predicted_recall_of_mp_stable"],
                           "frozen_thresholds": thr},
            "observed": {"pool_reduction": observed_reduction, "recall_of_mp_stable": observed_recall},
            "verdict": verdict,
            "controls_passed": controls_passed, "n_missing_from_cache": n_missing,
            "counts": {"n_pool": manifest["n_pool"], "n_screened": manifest["n_screened"],
                       "n_retained": manifest["n_retained"], "n_deprioritized": manifest["n_deprioritized"],
                       "validation_queue_size": len(manifest["validation_queue"]),
                       "retained_fraction": retained_fraction},
            "retrospective_mp_metrics": {
                "label": "retrospective_calibration_metrics (in-sample, MP GGA/GGA+U hull <= 0.05 eV/atom)",
                "warning": IN_SAMPLE_WARNING,
                "mp_hull_column": gt_col,
                **{k: _finite(retro[k]) for k in ("tp", "fp", "fn", "tn", "retrospective_precision",
                                                   "retrospective_recall", "n_with_ground_truth")},
            },
            "retrospective_summary_sentence": (
                f"Retrospective in-sample comparison with MP labels ({gt_col} <= "
                f"{_ta.GROUND_TRUTH_CRITERION_EV_PER_ATOM} eV/atom) on the calibration pool, not prospective "
                f"and not DFT validation: TP {retro['tp']}, FP {retro['fp']}, FN {retro['fn']}, TN {retro['tn']}; "
                f"in-sample precision {_fmt(_finite(retro['retrospective_precision']))}, "
                f"in-sample recall {_fmt(_finite(retro['retrospective_recall']))}."
            ),
            "deterministic_decision_signal": signal,
            "allowed_next_decisions": list(NEXT_DECISIONS),
            "required_next_validation": REQUIRED_NEXT_VALIDATION,
            "spec_expected_learning": spec["expected_learning"],
            "spec_stop_rule": spec["stop_rule"],
            "traceability": {"run_id": int(run_id), "evaluation_run_id": eval_run_id,
                             "spec_id": manifest["spec_id"], "hypothesis_id": spec["hypothesis_id"],
                             "policy_calibration_run_id": manifest["policy_calibration_run_id"],
                             "candidate_ids": [c["material_id"] for c in manifest["candidates"]]},
            "caveat": _ta.CAVEAT,
        }
        _write_once(Path(RUNS_DIR) / f"run_{int(run_id)}_evaluation.json", evaluation)
        return {k: v for k, v in evaluation.items() if k != "traceability"} | {
            "n_candidate_ids": len(evaluation["traceability"]["candidate_ids"]),
            "artifact": f"experiments/runs/run_{int(run_id)}_evaluation.json",
        }
    except Exception as exc:
        return _err(exc)


def record_next_decision(
    run_id: int,
    next_decision: str,
    what_was_learned: str,
    rationale: str,
    proposed_rule_change: str = "none",
) -> dict:
    """Record the Analyst's next decision (agent_generated) and write the demo summary."""
    try:
        ev_path = Path(RUNS_DIR) / f"run_{int(run_id)}_evaluation.json"
        if not ev_path.exists():
            return _err("evaluate_iteration has not been run for this run_id")
        ev = _read(ev_path)
        if next_decision not in NEXT_DECISIONS:
            return _err(f"next_decision must be one of {NEXT_DECISIONS}")
        rule = (proposed_rule_change or "none").strip()[:800]
        if not ev["controls_passed"] and rule.lower() != "none":
            return _err("controls failed: proposed_rule_change must be 'none'")
        violations = sorted({f"{name}:{field}" for field, text in
                             (("what_was_learned", what_was_learned), ("rationale", rationale),
                              ("proposed_rule_change", rule))
                             for name in scan_claims(str(text))})
        if violations:
            return {**_err("boundary_language: rewrite these fields and call record_next_decision again; "
                           "nothing was recorded"),
                    "violations": violations,
                    "guidance": ("When citing recall, precision, TP/FP/FN/TN, MP-stable labels or missed candidates, "
                                 "say 'retrospective in-sample'. Use 'RETAINED BY CALIBRATED SCREENING' / "
                                 "'DEPRIORITIZED BY SCREENING'; never call CHGNet DFT validation or claim "
                                 "generalization. You may copy retrospective_summary_sentence verbatim.")}
        decision = {
            "run_id": int(run_id), "label": "agent_generated",
            "next_decision": next_decision,
            "matches_deterministic_signal": next_decision == ev["deterministic_decision_signal"],
            "deterministic_decision_signal": ev["deterministic_decision_signal"],
            "what_was_learned": str(what_was_learned)[:1500],
            "rationale": str(rationale)[:1500],
            "proposed_rule_change": rule,
            "rule_change_status": "proposed_not_applied",
        }
        _write_once(Path(RUNS_DIR) / f"run_{int(run_id)}_next_decision.json", decision)
        summary_path = Path(REPORTS_DIR) / f"iteration_run{int(run_id)}.md"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(_summary_md(ev, decision), encoding="utf-8")
        return {**decision, "summary_report": str(summary_path.relative_to(_REPO_ROOT))
                if summary_path.is_relative_to(_REPO_ROOT) else str(summary_path)}
    except Exception as exc:
        return _err(exc)


def _fmt(x) -> str:
    return "n/a" if x is None else (f"{x:.3f}" if isinstance(x, float) else str(x))


def _summary_md(ev: dict, dec: dict) -> str:
    c, r, t = ev["counts"], ev["retrospective_mp_metrics"], ev["traceability"]
    return "\n".join([
        f"# Discovery iteration: run {ev['run_id']}",
        "",
        "**Scientific question:** Can the calibrated CHGNet screening layer reduce the downstream "
        "expensive-validation pool while retaining a high fraction of promising materials?",
        "",
        f"**Hypothesis (agent_generated, hypothesis_id {ev['hypothesis_id']}):** {ev['hypothesis_statement']}",
        f"- Predicted pool reduction: {ev['prediction']['pool_reduction']}; observed: {ev['observed']['pool_reduction']}",
        f"- Predicted recall of MP-stable: {ev['prediction']['recall_of_mp_stable']}; observed: {ev['observed']['recall_of_mp_stable']}",
        f"- Verdict (computed): **{ev['verdict']}**",
        "",
        f"**Experiment chosen (spec_id {ev['spec_id']}):** {ev['chosen_test']}",
        "",
        "| Measure | Value |",
        "|---|---|",
        f"| Candidate pool | {c['n_pool']} (screened {c['n_screened']}) |",
        f"| RETAINED BY CALIBRATED SCREENING | {c['n_retained']} |",
        f"| DEPRIORITIZED BY SCREENING | {c['n_deprioritized']} |",
        f"| Validation queue size | {c['validation_queue_size']} |",
        f"| Retained fraction | {_fmt(c['retained_fraction'])} |",
        f"| Controls passed | {ev['controls_passed']} |",
        "",
        f"**Retrospective MP metrics (in-sample, not prospective performance):** "
        f"precision {_fmt(r['retrospective_precision'])}, recall {_fmt(r['retrospective_recall'])} "
        f"(TP {r['tp']}, FP {r['fp']}, FN {r['fn']}, TN {r['tn']}). {r['warning']}",
        "",
        f"**What was learned (agent_generated):** {dec['what_was_learned']}",
        "",
        f"**Next decision (agent_generated):** {dec['next_decision']} "
        f"(deterministic signal: {dec['deterministic_decision_signal']}; match: {dec['matches_deterministic_signal']})",
        f"- Rationale: {dec['rationale']}",
        f"- Proposed rule change ({dec['rule_change_status']}): {dec['proposed_rule_change']}",
        f"- Required next validation: {ev['required_next_validation']}",
        "",
        f"Traceability: run_id {t['run_id']}, evaluation_run_id {t['evaluation_run_id']}, spec_id {t['spec_id']}, "
        f"hypothesis_id {t['hypothesis_id']}, policy calibration run {t['policy_calibration_run_id']}, "
        f"{len(t['candidate_ids'])} candidate IDs in experiments/runs/run_{t['run_id']}.json.",
        "",
        f"_{ev['caveat']}_",
        "",
    ])
