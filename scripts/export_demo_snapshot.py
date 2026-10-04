"""Export a judge-facing demo snapshot from existing artifacts. Read-only on lab science files.

    uv run python scripts/export_demo_snapshot.py --run-id 8

Writes web/data/snapshot.json only. Prefers experiments/runs/run_<id>*.json. When a
run file is missing, still writes a snapshot from the committed reports, the stability
policy, Arm A, and a read-only screen of reports/stability_calibration.csv.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lab import run_batch as rb
from lab import tools_api as ta
from lab.decision import load_policy
from lab.test_menu import get_budget, get_test_menu

QUESTION = (
    "Can the calibrated CHGNet screening layer reduce the downstream expensive-validation "
    "pool while retaining a high fraction of promising materials?"
)
POLICY_VS_RUN_WARNING = (
    "Run retrospective counts may differ from stability_policy.json (open GT-column issue). "
    "Show both; do not silently pick one."
)
ARM_A_HONEST_NOTE = (
    "Family-held-out composition proxy did not beat simple baselines on this split; "
    "CIs overlap. Do not claim acceleration from Arm A."
)
AGENTS = [
    {"id": "pi", "name": "PI", "role": "orchestrates; no lab tools", "handoff": "delegates"},
    {"id": "hypothesis", "name": "Hypothesis", "role": "preregisters prediction before the run",
     "handoff": "hypothesis_id"},
    {"id": "planner", "name": "Planner", "role": "chooses one test from the menu and registers the spec",
     "handoff": "spec_id"},
    {"id": "runner", "name": "Runner", "role": "screens the cached pool with controls; no ground truth",
     "handoff": "run_id"},
    {"id": "analyst", "name": "Analyst",
     "role": "compares the registered prediction with the run and records the next decision",
     "handoff": "evaluation_run_id"},
    {"id": "auditor", "name": "Auditor", "role": "integrity and boundary checks; APPROVE or VETO",
     "handoff": "audit_id"},
    {"id": "human_gate", "name": "Human gate", "role": "CLI approval only; agents cannot approve",
     "handoff": "approval_id"},
    {"id": "next_iteration_planner", "name": "Iteration 2 planner",
     "role": "plans the next iteration only after a recorded human decision", "handoff": "plan_id"},
]


def _read(path: Path) -> str | None:
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _read_json(path: Path) -> dict | None:
    text = _read(path)
    if text is None:
        return None
    return json.loads(text)


def _section(md: str, title: str) -> str:
    m = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)", md, re.M | re.S)
    return m.group(1).strip() if m else ""


def _opt_int(token: str | None) -> int | None:
    if token is None or token.strip().lower() in {"", "pending", "p", "null", "none"}:
        return None
    return int(token)


def _parse_discovery(md: str) -> dict:
    hyp = _section(md, "Hypothesis (agent_generated, registered before the run)")
    statement = ""
    predicted_reduction = None
    predicted_recall = None
    frozen = None
    for line in hyp.splitlines():
        s = line.strip()
        if s.startswith("AGENT HYPOTHESIS"):
            statement = s
        m = re.search(
            r"Predicted pool reduction: (\w+); predicted recall of MP-stable: (\w+); frozen thresholds: (\{.*\})",
            s,
        )
        if m:
            predicted_reduction, predicted_recall = m.group(1), m.group(2)
            frozen = json.loads(m.group(3))

    alts = _section(md, "Alternatives")
    tests = []
    reasoning = ""
    for line in alts.splitlines():
        s = line.strip()
        m = re.match(r"- (\w+) \(([^)]+)\): runnable_in_this_lab=(True|False)", s)
        if m:
            tests.append({
                "name": m.group(1),
                "tier": m.group(2),
                "runnable_in_this_lab": m.group(3) == "True",
            })
        if s.startswith("- Planner's reasoning"):
            reasoning = s.split(":", 1)[1].strip()

    exp = _section(md, "Experiment")
    chosen = None
    max_cand = None
    expected = None
    stop = None
    for line in exp.splitlines():
        s = line.strip()
        m = re.search(r"Selected: (\w+).*max_candidates \(upper bound\) (\d+)", s)
        if m:
            chosen, max_cand = m.group(1), int(m.group(2))
        if "expected learning" in s.lower():
            expected = s.split(":", 1)[1].strip()
        if s.lower().startswith("- stop rule"):
            stop = s.split(":", 1)[1].strip()

    result = _section(md, "Result")
    counts = re.search(
        r"Screened (\d+) of (\d+); RETAINED BY CALIBRATED SCREENING (\d+); "
        r"DEPRIORITIZED(?: BY SCREENING)? (\d+); validation queue (\d+)",
        result,
    )
    runtime = re.search(r"Runtime: ([0-9.]+) ms", result)
    cutoff_m = re.search(
        r"Cutoff ([0-9.eE+-]+) eV/atom, frozen convention (\w+), calibration run (\d+)",
        result,
    )
    controls_line = re.search(r"Controls: (passed|FAILED) \(([^)]+)\)", result)

    retro = _section(md, "Retrospective evidence (in-sample; NOT prospective performance)")
    metrics = re.search(
        r"TP (\d+), FP (\d+), FN (\d+), TN (\d+); precision ([0-9.]+), recall ([0-9.]+) "
        r"\(MP hull <= 0.05 eV/atom, column ([^)]+)\)",
        retro,
    )
    warning = ""
    for line in retro.splitlines():
        s = line.strip().lstrip("- ").strip()
        if s.lower().startswith("in-sample"):
            warning = s

    analyst = _section(md, "Analyst conclusion")
    verd = re.search(
        r"Verdict \(computed\): \*\*(\w+)\*\*; observed (\{.*?\});? vs predicted (\w+)/(\w+)",
        analyst,
    )
    # The published report has no semicolon between the JSON and "vs".
    if verd is None:
        verd = re.search(
            r"Verdict \(computed\): \*\*(\w+)\*\*; observed (\{.*?\}) vs predicted (\w+)/(\w+)",
            analyst,
        )

    learning = _section(md, "Learning")
    learned = ""
    lm = re.search(r"\(agent_generated\) (.+)", learning)
    if lm:
        learned = lm.group(1).strip()

    nxt = _section(md, "Next decision")
    decision = re.search(
        r"Proposed \(agent_generated\): (\S+) \(deterministic signal ([^;]+); match (True|False)\)",
        nxt,
    )
    rule = re.search(
        r"Proposed rule change: (.+?) \(status: ([^;]+); applied: (True|False)\)",
        nxt,
    )
    held = re.search(r"Next experiment: ([^,]+), status ([^:]+): (.+)", nxt)

    auditor = _section(md, "Auditor")
    au = re.search(r"(VETO|APPROVE) \(audit_id (\d+)\): (.+)", auditor)
    warnings = [ln.split(":", 1)[1].strip() for ln in auditor.splitlines() if ln.strip().startswith("- warning:")]
    checks_failed: list[str] = []
    reason = None
    if au:
        reason = au.group(3).strip()
        prefix = "critical checks failed: "
        if reason.startswith(prefix):
            checks_failed = [p.strip() for p in reason[len(prefix):].split(", ") if p.strip()]

    human = _section(md, "Human gate").strip()
    human_gate = {"status": "pending", "approver": None, "timestamp": None,
                  "approval_id": None, "channel": None}
    hm = re.search(
        r"(APPROVED|REJECTED) by (.+?) at (\S+) \(approval_id (\d+), channel ([^)]+)\)",
        human,
    )
    if hm:
        human_gate = {
            "status": "approved" if hm.group(1) == "APPROVED" else "rejected",
            "approver": hm.group(2).strip(),
            "timestamp": hm.group(3),
            "approval_id": int(hm.group(4)),
            "channel": hm.group(5).strip(),
        }

    trace = _section(md, "Traceability")
    ids_m = re.search(
        r"hypothesis_id (\w+); spec_id (\w+); run_id (\w+); evaluation_run_id (\w+); "
        r"audit_id (\w+); approval_id (\w+); plan_id (\w+); policy calibration run_id (\w+)",
        trace,
    )

    tp = fp = fn = tn = None
    precision = recall = None
    mp_col = None
    if metrics:
        tp, fp, fn, tn = (int(metrics.group(i)) for i in range(1, 5))
        # Integers are the recorded counts. Ratios are exact; the report prints them to 3 decimals.
        precision = tp / (tp + fp) if (tp + fp) else None
        recall = tp / (tp + fn) if (tp + fn) else None
        mp_col = metrics.group(7)

    observed = json.loads(verd.group(2)) if verd else None
    return {
        "statement": statement,
        "predicted_pool_reduction": predicted_reduction,
        "predicted_recall_of_mp_stable": predicted_recall,
        "frozen_thresholds": frozen,
        "tests_from_report": tests,
        "alternatives_considered": reasoning or None,
        "chosen_test": chosen,
        "max_candidates_upper_bound": max_cand,
        "expected_learning": expected,
        "stop_rule": stop,
        "n_screened": int(counts.group(1)) if counts else None,
        "n_pool": int(counts.group(2)) if counts else None,
        "n_retained": int(counts.group(3)) if counts else None,
        "n_deprioritized": int(counts.group(4)) if counts else None,
        "validation_queue_size": int(counts.group(5)) if counts else None,
        "wall_ms": float(runtime.group(1)) if runtime else None,
        "cutoff_ev_per_atom": float(cutoff_m.group(1)) if cutoff_m else None,
        "frozen_convention": cutoff_m.group(2) if cutoff_m else None,
        "policy_calibration_run_id": int(cutoff_m.group(3)) if cutoff_m else None,
        "controls_passed_report": (controls_line.group(1) == "passed") if controls_line else None,
        "control_ids": [c.strip() for c in controls_line.group(2).split(",")] if controls_line else [],
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": precision, "recall": recall, "mp_hull_column": mp_col, "warning": warning or None,
        "verdict": verd.group(1) if verd else None,
        "observed": observed,
        "what_was_learned": learned or None,
        "next_decision": decision.group(1) if decision else None,
        "deterministic_signal": decision.group(2).strip() if decision else None,
        "matches_signal": (decision.group(3) == "True") if decision else None,
        "proposed_rule_change": rule.group(1).strip() if rule else None,
        "rule_applied": (rule.group(3) == "True") if rule else False,
        "next_experiment": held.group(1).strip() if held else None,
        "next_experiment_status": held.group(2).strip() if held else None,
        "next_experiment_reason": held.group(3).strip() if held else None,
        "audit_status": au.group(1) if au else None,
        "audit_id": int(au.group(2)) if au else None,
        "audit_reason": reason,
        "checks_failed": checks_failed,
        "warnings": warnings,
        "human_gate": human_gate,
        "ids": None if ids_m is None else {
            "hypothesis_id": _opt_int(ids_m.group(1)),
            "spec_id": _opt_int(ids_m.group(2)),
            "run_id": _opt_int(ids_m.group(3)),
            "evaluation_run_id": _opt_int(ids_m.group(4)),
            "audit_id": _opt_int(ids_m.group(5)),
            "approval_id": _opt_int(ids_m.group(6)),
            "plan_id": _opt_int(ids_m.group(7)),
            "policy_calibration_run_id": _opt_int(ids_m.group(8)),
        },
    }


def _parse_iteration(md: str | None) -> str | None:
    if not md:
        return None
    m = re.search(r"- Rationale: (.+)", md)
    return m.group(1).strip() if m else None


def _why_from_reasoning(reasoning: str | None, name: str) -> str | None:
    if not reasoning:
        return None
    parts = re.split(r"(?=\b(?:composition_proxy|chgnet_triage|dft_validation):)", reasoning)
    for part in parts:
        if part.startswith(name + ":"):
            return part.split(":", 1)[1].strip() or None
    return None


def _controls(policy: dict, candidates: list) -> list[dict]:
    cutoff = policy["screening_cutoff_ev_per_atom"]
    pos = rb.CandidateStabilityResult("ctrl_pos", "control", 0.0)
    neg = rb.CandidateStabilityResult("ctrl_neg", "control", cutoff + 1.0)
    p, n = rb.screen_batch([pos, neg], policy)
    first = [c.screening_decision for c in rb.screen_batch(candidates, policy)]
    second = [c.screening_decision for c in rb.screen_batch(candidates, policy)]
    rows = [
        {"control_id": "positive_hull_0", "expected": "RETAIN", "observed": p.screening_decision},
        {"control_id": "negative_hull_cutoff_plus_1", "expected": "DEPRIORITIZE", "observed": n.screening_decision},
        {"control_id": "determinism_rescreen", "expected": "identical",
         "observed": "identical" if first == second else "different"},
        {"control_id": "frozen_convention", "expected": "corrected", "observed": policy["frozen_convention"]},
    ]
    for row in rows:
        row["passed"] = row["expected"] == row["observed"]
    return rows


def _candidates_from_cache(policy: dict) -> tuple[list[dict], list[str], list]:
    """Same deployed path as the Runner: csv float() cache + screen_batch. No ledger writes."""
    col = f"chgnet_hull_{policy['frozen_convention']}"
    cache = ta._load_cache(ROOT / "reports" / "stability_calibration.csv", col)
    raw = [
        rb.CandidateStabilityResult(m, cache[m]["formula"], float(cache[m][col]))
        for m in sorted(cache)
    ]
    screened = rb.screen_batch(raw, policy)
    queue = rb.build_validation_queue(screened)
    rank = {c.candidate_id: i + 1 for i, c in enumerate(queue)}
    rows = [
        {
            "material_id": c.candidate_id,
            "formula": c.formula,
            "chgnet_hull_ev_per_atom": c.chgnet_hull_ev_per_atom,
            "screening_decision": c.screening_decision,
            "validation_priority": c.validation_priority,
            "queue_rank": rank.get(c.candidate_id),
        }
        for c in screened
    ]
    return rows, [c.candidate_id for c in queue[:5]], raw


def _candidates_from_manifest(manifest: dict) -> tuple[list[dict], list[str]]:
    queue = list(manifest.get("validation_queue") or [])
    rank = {mid: i + 1 for i, mid in enumerate(queue)}
    rows = []
    for c in manifest.get("candidates") or []:
        mid = c.get("material_id") or c.get("candidate_id")
        rows.append({
            "material_id": mid,
            "formula": c.get("formula"),
            "chgnet_hull_ev_per_atom": c.get("chgnet_hull_ev_per_atom"),
            "screening_decision": c.get("screening_decision"),
            "validation_priority": c.get("validation_priority"),
            "queue_rank": rank.get(mid),
        })
    return rows, queue[:5]


def _latest_audit(run_id: int) -> dict | None:
    runs = ROOT / "experiments" / "runs"
    if not runs.is_dir():
        return None
    paths = sorted(runs.glob(f"run_{int(run_id)}_audit_*.json"),
                   key=lambda p: int(p.stem.rsplit("_", 1)[-1]))
    return _read_json(paths[-1]) if paths else None


def _menu_tests(report_tests: list[dict], reasoning: str | None, chosen: str | None) -> list[dict]:
    menu = get_test_menu(ROOT / "reports")
    menu_tests = menu.get("tests") if isinstance(menu, dict) else None
    if not menu_tests:
        out = []
        for t in report_tests:
            name = t["name"]
            is_chosen = name == chosen
            out.append({
                "name": name,
                "tier": t.get("tier"),
                "runnable_in_this_lab": t.get("runnable_in_this_lab"),
                "why_chosen": "Selected." if is_chosen else None,
                "why_not_chosen": None if is_chosen else _why_from_reasoning(reasoning, name),
                "metrics": None,
            })
        return out
    out = []
    for name, spec in menu_tests.items():
        is_chosen = name == chosen
        metrics = {k: v for k, v in spec.items() if k not in {"tier", "runnable_in_this_lab"}}
        out.append({
            "name": name,
            "tier": spec.get("tier"),
            "runnable_in_this_lab": spec.get("runnable_in_this_lab"),
            "why_chosen": "Selected." if is_chosen else None,
            "why_not_chosen": None if is_chosen else _why_from_reasoning(reasoning, name),
            "metrics": metrics or None,
        })
    return out


def _budget() -> dict:
    raw = get_budget(ROOT / "experiments" / "budget_001.yaml", ROOT / "reports")
    if not isinstance(raw, dict) or "error" in raw:
        return {
            "budget_id": None,
            "total_compute_budget_s": None,
            "candidate_pool_size": None,
            "affordable_candidates": None,
            "cost_basis_note": None,
            "set_by": "human",
        }
    return {
        "budget_id": raw.get("budget_id"),
        "total_compute_budget_s": raw.get("total_compute_budget_s"),
        "candidate_pool_size": raw.get("candidate_pool_size"),
        "affordable_candidates": raw.get("affordable_chgnet_candidates_upper_bound"),
        "cost_basis_note": raw.get("cost_basis_note"),
        "set_by": raw.get("set_by") or "human",
    }


def _arm_a() -> dict:
    data = _read_json(ROOT / "reports" / "arm_a_results_run1.json") or {}
    return {
        "n_samples": data.get("n_samples"),
        "n_positive": data.get("n_positive"),
        "base_rate": data.get("base_rate"),
        "point_estimates": data.get("point_estimates"),
        "verdicts_summary": data.get("verdicts"),
        "honest_note": ARM_A_HONEST_NOTE,
    }


def _policy_counts(policy: dict) -> dict:
    return {k: policy.get(k) for k in (
        "tp", "fp", "fn", "tn", "recall_at_cutoff", "precision_at_cutoff",
    )}


def build(run_id: int) -> dict:
    runs = ROOT / "experiments" / "runs"
    manifest = _read_json(runs / f"run_{run_id}.json")
    evaluation = _read_json(runs / f"run_{run_id}_evaluation.json")
    decision = _read_json(runs / f"run_{run_id}_next_decision.json")
    record = _read_json(runs / f"run_{run_id}_decision_record.json")
    plan = _read_json(runs / f"run_{run_id}_iteration2_plan.json")
    audit = _latest_audit(run_id)

    discovery_path = ROOT / "reports" / f"discovery_report_run{run_id}.md"
    iteration_path = ROOT / "reports" / f"iteration_run{run_id}.md"
    discovery_md = _read(discovery_path) or ""
    iteration_md = _read(iteration_path) or ""
    parsed = _parse_discovery(discovery_md) if discovery_md else {}

    if manifest is None:
        print(
            f"experiments/runs/run_{run_id}.json is not in this checkout. "
            "Run an iteration locally first if you need the ledger artifacts. "
            "Writing snapshot from reports/discovery_report_run"
            f"{run_id}.md, stability_policy.json, arm_a, and the calibration CSV.",
            file=sys.stderr,
        )

    policy = load_policy(ROOT / "reports" / "stability_policy.json")
    if manifest is not None:
        candidates, top5 = _candidates_from_manifest(manifest)
        controls = []
        for c in manifest.get("controls") or []:
            controls.append({
                "control_id": c.get("control_id"),
                "expected": c.get("expected"),
                "observed": c.get("observed"),
                "passed": c.get("passed", c.get("expected") == c.get("observed")),
            })
        n_pool = manifest.get("n_pool")
        n_screened = manifest.get("n_screened")
        n_retained = manifest.get("n_retained")
        n_deprioritized = manifest.get("n_deprioritized")
        queue_size = len(manifest.get("validation_queue") or [])
        wall_ms = manifest.get("wall_ms")
        cutoff = manifest.get("screening_cutoff_ev_per_atom")
        frozen = manifest.get("frozen_convention")
        controls_passed = manifest.get("controls_passed")
        candidates_source = f"experiments/runs/run_{run_id}.json"
    else:
        candidates, top5, raw = _candidates_from_cache(policy)
        controls = _controls(policy, raw)
        n_ret = sum(1 for c in candidates if c["screening_decision"] == "RETAIN")
        n_dep = sum(1 for c in candidates if c["screening_decision"] == "DEPRIORITIZE")
        if parsed.get("n_retained") not in (None, n_ret) or parsed.get("n_deprioritized") not in (None, n_dep):
            print(
                f"WARNING: screen_batch retained {n_ret} / deprioritized {n_dep}; "
                f"discovery report says {parsed.get('n_retained')} / {parsed.get('n_deprioritized')}.",
                file=sys.stderr,
            )
        n_pool = len(candidates)
        n_screened = len(candidates)
        n_retained = n_ret
        n_deprioritized = n_dep
        queue_size = sum(1 for c in candidates if c["queue_rank"] is not None)
        wall_ms = parsed.get("wall_ms")
        cutoff = policy.get("screening_cutoff_ev_per_atom")
        frozen = policy.get("frozen_convention")
        controls_passed = all(c["passed"] for c in controls) if controls else parsed.get("controls_passed_report")
        candidates_source = (
            "reports/stability_calibration.csv via lab.run_batch.screen_batch "
            "and reports/stability_policy.json (run JSON absent)"
        )

    if evaluation is not None:
        rm = evaluation.get("retrospective_mp_metrics") or {}
        pred = evaluation.get("prediction") or {}
        evaluation_block = {
            "verdict": evaluation.get("verdict"),
            "observed": evaluation.get("observed"),
            "predicted": {
                "pool_reduction": pred.get("pool_reduction"),
                "recall_of_mp_stable": pred.get("recall_of_mp_stable"),
                "frozen_thresholds": pred.get("frozen_thresholds"),
            },
            "retrospective_mp_metrics": {
                "tp": rm.get("tp"),
                "fp": rm.get("fp"),
                "fn": rm.get("fn"),
                "tn": rm.get("tn"),
                "precision": rm.get("retrospective_precision"),
                "recall": rm.get("retrospective_recall"),
                "mp_hull_column": rm.get("mp_hull_column"),
                "warning": rm.get("warning"),
            },
            "in_sample": True,
        }
        hyp_statement = evaluation.get("hypothesis_statement") or parsed.get("statement")
        pred_reduction = pred.get("pool_reduction") or parsed.get("predicted_pool_reduction")
        pred_recall = pred.get("recall_of_mp_stable") or parsed.get("predicted_recall_of_mp_stable")
        frozen_thr = pred.get("frozen_thresholds") or parsed.get("frozen_thresholds")
    else:
        evaluation_block = {
            "verdict": parsed.get("verdict"),
            "observed": parsed.get("observed"),
            "predicted": {
                "pool_reduction": parsed.get("predicted_pool_reduction"),
                "recall_of_mp_stable": parsed.get("predicted_recall_of_mp_stable"),
                "frozen_thresholds": parsed.get("frozen_thresholds"),
            },
            "retrospective_mp_metrics": {
                "tp": parsed.get("tp"),
                "fp": parsed.get("fp"),
                "fn": parsed.get("fn"),
                "tn": parsed.get("tn"),
                "precision": parsed.get("precision"),
                "recall": parsed.get("recall"),
                "mp_hull_column": parsed.get("mp_hull_column"),
                "warning": parsed.get("warning"),
            },
            "in_sample": True,
        }
        hyp_statement = parsed.get("statement")
        pred_reduction = parsed.get("predicted_pool_reduction")
        pred_recall = parsed.get("predicted_recall_of_mp_stable")
        frozen_thr = parsed.get("frozen_thresholds")

    if decision is not None:
        learning = {
            "what_was_learned": decision.get("what_was_learned"),
            "next_decision": decision.get("next_decision"),
            "deterministic_signal": decision.get("deterministic_decision_signal"),
            "matches_signal": decision.get("matches_deterministic_signal"),
            "proposed_rule_change": decision.get("proposed_rule_change"),
            "rule_applied": False,
            "rationale": decision.get("rationale"),
        }
    else:
        learning = {
            "what_was_learned": parsed.get("what_was_learned"),
            "next_decision": parsed.get("next_decision"),
            "deterministic_signal": parsed.get("deterministic_signal"),
            "matches_signal": parsed.get("matches_signal"),
            "proposed_rule_change": parsed.get("proposed_rule_change"),
            "rule_applied": False if parsed.get("rule_applied") is None else parsed.get("rule_applied"),
            "rationale": _parse_iteration(iteration_md),
        }

    if audit is not None:
        auditor = {
            "audit_status": audit.get("audit_status"),
            "audit_id": audit.get("audit_id"),
            "veto": audit.get("veto"),
            "reason": audit.get("reason"),
            "checks_failed": audit.get("checks_failed"),
            "warnings": audit.get("warnings"),
            "promotion_allowed": audit.get("promotion_allowed"),
        }
    else:
        status = parsed.get("audit_status")
        veto = True if status == "VETO" else (False if status == "APPROVE" else None)
        auditor = {
            "audit_status": status,
            "audit_id": parsed.get("audit_id"),
            "veto": veto,
            "reason": parsed.get("audit_reason"),
            "checks_failed": parsed.get("checks_failed") or [],
            "warnings": parsed.get("warnings") or [],
            "promotion_allowed": (not veto) if veto is not None else None,
        }

    if record is not None:
        approved = record.get("status") == "human_approved" or record.get("human_approved") is True
        rejected = record.get("status") == "human_rejected" or record.get("human_approved") is False
        human_gate = {
            "status": "approved" if approved else ("rejected" if rejected else "pending"),
            "approver": record.get("approver"),
            "timestamp": record.get("timestamp"),
            "approval_id": record.get("approval_id"),
            "channel": record.get("channel"),
        }
    else:
        human_gate = parsed.get("human_gate") or {
            "status": "pending", "approver": None, "timestamp": None,
            "approval_id": None, "channel": None,
        }

    if plan is not None:
        nxt = plan.get("next_experiment") or {}
        if isinstance(nxt, str):
            nxt = {"next_experiment": nxt, "reason": None}
        iteration2 = {
            "status": plan.get("status") or "pending",
            "focus": plan.get("focus"),
            "focus_size": plan.get("focus_size"),
            "previous_pool_size": plan.get("previous_pool_size"),
            "next_experiment": nxt.get("next_experiment"),
            "experiment_status": nxt.get("status"),
            "reason": nxt.get("reason"),
            "plan_id": plan.get("plan_id"),
        }
    else:
        iteration2 = {
            "status": "pending",
            "focus": None,
            "focus_size": None,
            "previous_pool_size": None,
            "next_experiment": parsed.get("next_experiment"),
            "experiment_status": parsed.get("next_experiment_status"),
            "reason": parsed.get("next_experiment_reason"),
            "plan_id": None,
        }

    ids = parsed.get("ids") or {
        "hypothesis_id": None, "spec_id": None, "run_id": run_id,
        "evaluation_run_id": None, "audit_id": None, "approval_id": None,
        "plan_id": None, "policy_calibration_run_id": policy.get("calibration_run_id"),
    }
    ids["run_id"] = run_id
    if manifest is not None:
        ids["hypothesis_id"] = manifest.get("hypothesis_id", ids.get("hypothesis_id"))
        ids["spec_id"] = manifest.get("spec_id", ids.get("spec_id"))
        ids["policy_calibration_run_id"] = manifest.get(
            "policy_calibration_run_id", ids.get("policy_calibration_run_id"))
    if evaluation is not None:
        ids["evaluation_run_id"] = evaluation.get("evaluation_run_id", ids.get("evaluation_run_id"))
        ids["hypothesis_id"] = evaluation.get("hypothesis_id", ids.get("hypothesis_id"))
        ids["spec_id"] = evaluation.get("spec_id", ids.get("spec_id"))
    if auditor.get("audit_id") is not None:
        ids["audit_id"] = auditor["audit_id"]
    ids["approval_id"] = human_gate.get("approval_id")
    ids["plan_id"] = iteration2.get("plan_id")
    if ids.get("policy_calibration_run_id") is None:
        ids["policy_calibration_run_id"] = policy.get("calibration_run_id")

    chosen = None
    if manifest is not None:
        chosen = manifest.get("chosen_test")
    if evaluation is not None and evaluation.get("chosen_test"):
        chosen = evaluation["chosen_test"]
    chosen = chosen or parsed.get("chosen_test") or "chgnet_triage"

    # Unknowns live on the experiment spec in the ledger, not in the markdown report.
    unknowns = None
    alternatives = parsed.get("alternatives_considered")
    expected = parsed.get("expected_learning")
    stop_rule = parsed.get("stop_rule")
    max_cand = parsed.get("max_candidates_upper_bound")

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "caveat": ta.CAVEAT,
        "priority_note": ta.PRIORITY_NOTE,
        "evidence_tier": ta.TIER,
        "question": QUESTION,
        "run_id": run_id,
        "candidates_source": candidates_source,
        "repo_url": "https://github.com/rohanjones1/SuperCon",
        "ids": ids,
        "hypothesis": {
            "statement": hyp_statement,
            "predicted_pool_reduction": pred_reduction,
            "predicted_recall_of_mp_stable": pred_recall,
            "frozen_thresholds": frozen_thr,
            "agent_generated": True,
        },
        "planner": {
            "chosen_test": chosen,
            "alternatives_considered": alternatives,
            "expected_learning": expected,
            "stop_rule": stop_rule,
            "unknowns": unknowns,
            "max_candidates_upper_bound": max_cand,
            "tests": _menu_tests(parsed.get("tests_from_report") or [], alternatives, chosen),
        },
        "budget": _budget(),
        "result": {
            "n_pool": n_pool,
            "n_screened": n_screened,
            "n_retained": n_retained,
            "n_deprioritized": n_deprioritized,
            "validation_queue_size": queue_size,
            "validation_queue_top5": top5,
            "controls": controls,
            "controls_passed": controls_passed,
            "wall_ms": wall_ms,
            "timing_note": ta.TIMING_NOTE,
            "cutoff_ev_per_atom": cutoff,
            "frozen_convention": frozen,
        },
        "candidates": candidates,
        "evaluation": evaluation_block,
        "learning": learning,
        "auditor": auditor,
        "human_gate": human_gate,
        "iteration2": iteration2,
        "arm_a": _arm_a(),
        "policy_vs_run_warning": POLICY_VS_RUN_WARNING,
        "policy_counts": _policy_counts(policy),
        "agents": AGENTS,
        "reports": {
            "discovery_markdown": discovery_md,
            "iteration_markdown": iteration_md,
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Export web/data/snapshot.json for the static demo.")
    ap.add_argument("--run-id", type=int, default=8)
    args = ap.parse_args()
    snapshot = build(args.run_id)
    out = ROOT / "web" / "data" / "snapshot.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
    r = snapshot["result"]
    ev = snapshot["evaluation"]["retrospective_mp_metrics"]
    print(f"wrote {out.relative_to(ROOT)}")
    print(
        f"run {snapshot['run_id']}: screened {r['n_screened']} "
        f"retained {r['n_retained']} deprioritized {r['n_deprioritized']} "
        f"queue {r['validation_queue_size']}"
    )
    print(
        f"retrospective tp {ev['tp']} fp {ev['fp']} fn {ev['fn']} tn {ev['tn']} "
        f"P {ev['precision']} R {ev['recall']} verdict {snapshot['evaluation']['verdict']} "
        f"audit {snapshot['auditor']['audit_status']} human {snapshot['human_gate']['status']}"
    )
    print(f"candidates_source: {snapshot['candidates_source']}")


if __name__ == "__main__":
    main()
